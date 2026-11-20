"""按 sources.json 的清单采集官网/涉海机构新闻：站内检索优先、列表页兜底 → 关键词过滤 → 写 MySQL。

设计要点
--------
1. **一切靠配置**：源清单在 sources.json（含每个源的 `search` 块），关键词在 keywords.json，
   代码里不写死任何站点。
2. **检索优先**：每个源先走 `search.type` 指定的站内检索（api / results_page / browser），
   检索不可用、失败或 0 条时**自动回退该源的列表页**；两条路都失败才算该源失败。
3. **词表口径**：`keywords.global` 同时是**检索词**（每个源把每个词各搜一遍）与**回退过滤词**
   （`--keyword` 覆盖时两者一起换）；`per_source` 只在回退列表页时**追加**到过滤词里，
   不改检索词。命中词按来源标注（`global` / `per_source`），报告里能看出某条是按哪个词留的。
4. **入库**：写 MySQL（每源一表），接口来自 gov_news/db.py（延迟导入）；默认不写 out/*.json，
   需要留证据或回灌时用 `--dump-records` 显式导出。
5. **退出码**：有任一源失败返回 1；部分成功不算失败，但会在报告里标出。
6. **礼貌抓取**：全串行 + 请求间隔（默认 1s，`--delay` 可调大）；只有 `search.proxy=true` 的源
   （目前仅 scspi）才会用 `--proxy` 指定的代理，国内源一律直连。

用法（仓库根目录执行）
----------------------
    # 全部源，每源 2 篇，入库
    uv run --isolated --extra ai --with pymysql python gov_news/crawl.py --limit 2

    # 只跑纯静态检索的源（mod / mil81 / people）
    uv run --isolated --extra ai python gov_news/crawl.py --sources mod,mil81,people --limit 2 --no-db

    # 无站内检索的源：报告会标「回退列表页」
    uv run --isolated --extra ai python gov_news/crawl.py --sources ccg,nanhai --limit 2 --no-db

    # scspi 必须代理，不加 --proxy 会失败并回退
    uv run --isolated --extra ai python gov_news/crawl.py --sources scspi --limit 2 --proxy http://127.0.0.1:7897 --no-db

    # 临时换检索词（覆盖 keywords.json 的 global）
    uv run --isolated --extra ai python gov_news/crawl.py --keyword 南海,黄岩岛 --limit 3 --no-db

    # 只测"能不能取到候选"（不抓详情、不入库，快）
    uv run --isolated --extra ai python gov_news/crawl.py --list-only

    # 把结果导出成 JSON（默认不导出；产物可直接被 --from-json 回灌）
    uv run --isolated --extra ai python gov_news/crawl.py --sources people --limit 3 --no-db --dump-records out/people.json

    # 用已有 JSON 回灌入库
    uv run --isolated --extra ai --with pymysql python gov_news/crawl.py --from-json gov_news/out/people.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

from scrapling import Selector
from scrapling.fetchers import Fetcher, StealthyFetcher, StealthySession

HERE = Path(__file__).resolve().parent

SEARCH_TYPES = ("api", "results_page", "browser", "none")
DEFAULT_SEARCH_PAGES = 5
MAX_SEARCH_PAGES = 20
DEFAULT_SEARCH_LIMIT = 300
DEFAULT_FETCH_LIMIT = 5
DEFAULT_LIST_PAGES = 3
MAX_LIST_PAGES = 50
DEFAULT_DELAY = 1.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_DB_NAME = "newscrawl"
SETTINGS_PATH = HERE / "settings.json"
# 候选收集的硬护栏：--search-limit 0/负数（不设上限）时也只收这么多，避免 people 这种
# 站内命中上万条的源把内存和耗时拉爆（超限会打印提醒并停止继续翻页）
LIST_CANDIDATE_GUARD = 5000

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_DATE_PATTERNS = [
    re.compile(r"(20\d{2}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)"),
    re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}(?::\d{2})?)"),
    re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日)"),
    re.compile(r"(20\d{2}-\d{1,2}-\d{1,2})"),
]
# 正文兜底时要跳过的容器（导航/页脚/推荐位这类会污染正文）
_NOISE_CONTAINER = re.compile(
    r"(nav|menu|footer|header|side|share|comment|recommend|crumbs|breadcrumb|link|banner)", re.I
)


# ---------------------------------------------------------------- 读配置


def load_config(path: Path) -> Dict[str, Any]:
    # utf-8-sig：Windows 记事本另存为 UTF-8 会带 BOM，用 utf-8 读会在第 1 列就解析失败
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    # 允许在 JSON 里写 _readme 之类的注释字段（json 不支持注释，就用下划线键代替）
    return {k: v for k, v in data.items() if not k.startswith("_")}


@dataclass
class RunSettings:
    """settings.json 的内容（缺字段用内置默认；文件不存在时整份都是默认值）。

    优先级：命令行 > settings.json > 源配置(sources.json) > 代码内置默认。
    这里只承载 settings 这一层，命令行与源配置的覆盖在 resolve_* 里做。
    """

    search_pages: int = DEFAULT_SEARCH_PAGES
    search_limit: int = DEFAULT_SEARCH_LIMIT
    fetch_limit: int = DEFAULT_FETCH_LIMIT
    list_pages: int = DEFAULT_LIST_PAGES
    delay_seconds: float = DEFAULT_DELAY
    timeout_seconds: float = DEFAULT_TIMEOUT
    proxy: str = ""
    db: Dict[str, Any] = field(default_factory=dict)
    source_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def limit_for(self, source_key: str, field: str, fallback: int) -> int:
        """源级覆盖（source_overrides.<key>.<field>）> settings 全局 > 调用方给的兜底。"""
        override = (self.source_overrides or {}).get(source_key) or {}
        value = override.get(field)
        if value is None:
            value = getattr(self, field)
        return int(value if value is not None else fallback)


def load_settings(path: Path) -> Tuple[RunSettings, str]:
    """读 settings.json。文件不存在不报错（全部走源配置/内置默认）；解析失败也不静默吞掉。"""
    if not path.exists():
        return RunSettings(), f"{path} 不存在（全部走源配置/内置默认）"
    try:
        data = load_config(path)
    except Exception as exc:  # noqa: BLE001 - 配置错误要大声报错，不能装作没读到
        return RunSettings(), f"{path} 解析失败：{type(exc).__name__}: {exc}"
    return (
        RunSettings(
            search_pages=int(data.get("search_pages", DEFAULT_SEARCH_PAGES)),
            search_limit=int(data.get("search_limit", DEFAULT_SEARCH_LIMIT)),
            fetch_limit=int(data.get("fetch_limit", DEFAULT_FETCH_LIMIT)),
            list_pages=int(data.get("list_pages", DEFAULT_LIST_PAGES)),
            delay_seconds=float(data.get("delay_seconds", DEFAULT_DELAY)),
            timeout_seconds=float(data.get("timeout_seconds", DEFAULT_TIMEOUT)),
            proxy=str(data.get("proxy") or ""),
            db=dict(data.get("db") or {}),
            source_overrides=dict(data.get("source_overrides") or {}),
        ),
        str(path),
    )


def resolve_search_limit(args: argparse.Namespace, settings: RunSettings) -> int:
    """检索阶段每个源最多收多少候选。

    命令行只接受正数（0 = "没传"，回落到 settings）；"不设上限"是 settings 里的 0/负数，
    此时仍受 LIST_CANDIDATE_GUARD 护栏约束。
    """
    return int(args.search_limit) if args.search_limit else int(settings.search_limit)


def resolve_fetch_limit(source: Dict[str, Any], args: argparse.Namespace, settings: RunSettings) -> int:
    """详情抓取篇数：命令行 > settings(source_overrides 带源配置兜底) > settings 全局 > 源配置 limit。

    source_overrides 是给单源调量的旋钮（如 xinhua 走浏览器，单源调小），它要能压过源配置的
    `limit`，否则调不动。
    """
    if args.fetch_limit:
        return int(args.fetch_limit)
    source_limit = int(source.get("limit", DEFAULT_FETCH_LIMIT))
    return max(0, settings.limit_for(source["key"], "fetch_limit", source_limit))


def resolve_list_pages(source: Dict[str, Any], args: argparse.Namespace, settings: RunSettings) -> int:
    """回退列表页翻几页：命令行 > 源配置 > settings(source_overrides) > 内置默认。"""
    if args.list_pages:
        pages = int(args.list_pages)
    elif source.get("list_pages") is not None:
        pages = int(source["list_pages"])
    else:
        pages = settings.limit_for(source["key"], "list_pages", DEFAULT_LIST_PAGES)
    return max(1, min(MAX_LIST_PAGES, pages))


def resolve_delay(args: argparse.Namespace, settings: RunSettings) -> float:
    return float(args.delay) if args.delay else float(settings.delay_seconds)


def resolve_timeout(args: argparse.Namespace, settings: RunSettings) -> float:
    return float(args.timeout) if args.timeout else float(settings.timeout_seconds)


def resolve_global_proxy(args: argparse.Namespace, settings: RunSettings, where: str) -> Optional[str]:
    """代理地址：命令行 > settings.proxy；最终仍只对 search.proxy=true 的源生效。"""
    if args.proxy:
        return args.proxy
    if settings.proxy:
        print(f"    （代理来自 {where} 的 proxy：{settings.proxy}；仍只对 search.proxy=true 的源生效）")
        return settings.proxy
    return None


def resolve_db_params(args: argparse.Namespace, settings: RunSettings) -> Dict[str, Any]:
    """入库连接参数：命令行 > settings.json 的 db 区块 > db.py 自己的默认。

    **settings.json 的 db 区块只放 host/port/user/name，不放密码**；密码只从
    `--db-password` 或环境变量 `GOV_NEWS_DB_PASSWORD` 来，没给就交给 db.py 的默认值。
    """
    db_conf = settings.db or {}
    params: Dict[str, Any] = {"db_name": args.db_name or str(db_conf.get("name") or DEFAULT_DB_NAME)}
    if db_conf.get("host"):
        params["host"] = str(db_conf["host"])
    if db_conf.get("port"):
        params["port"] = int(db_conf["port"])
    if db_conf.get("user"):
        params["user"] = str(db_conf["user"])
    password = args.db_password or os.environ.get("GOV_NEWS_DB_PASSWORD") or ""
    if password:
        params["password"] = password
    return params


def load_keywords(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_config(Path(args.keywords)) if Path(args.keywords).exists() else {}
    if args.keyword:
        config["global"] = [k.strip() for k in args.keyword.split(",") if k.strip()]
    config.setdefault("global", [])
    config.setdefault("per_source", {})
    config.setdefault("exclude", [])
    config.setdefault("match", "any")
    return config


def select_sources(config: Dict[str, Any], wanted: str) -> List[Dict[str, Any]]:
    """挑出要跑的源；显式点名但 enabled=false 的源给出提示（如已废弃的 mod_search_scs）。"""
    wanted_keys = {k.strip() for k in (wanted or "").split(",") if k.strip()}
    all_keys = {s["key"] for s in config["sources"]}
    unknown = wanted_keys - all_keys
    if unknown:
        print(f"⚠ --sources 里有配置中不存在的 key：{', '.join(sorted(unknown))}")
    sources = []
    for source in config["sources"]:
        if wanted_keys and source["key"] not in wanted_keys:
            continue
        if not source.get("enabled", True):
            if wanted_keys:
                print(
                    f"⚠ 源 {source['key']} 已禁用（enabled=false），跳过"
                    f"{'：' + source['deprecated_note'] if source.get('deprecated_note') else ''}"
                )
            continue
        sources.append(source)
    return sources


# ---------------------------------------------------------------- 通用工具


def strip_html(text: str) -> str:
    """清掉检索接口返回的高亮标签（形如 `<em>南海</em>`、`<span style=color:red>南海</span>`）并反转义实体。

    人民网的检索接口标题与摘要都带 `<em>` 高亮，不清洗就会把标签写进库。
    """
    if not text:
        return ""
    return html_unescape(_TAG.sub("", str(text))).strip()


def dig(obj: Any, path: str, default: Any = None) -> Any:
    """按 "data.resultList" 这样的点号路径取值，缺字段不抛异常；path 为空时直接给默认值。"""
    if not path:
        return default
    current = obj
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return default
        if current is None:
            return default
    return current


def fmt_epoch(value: Any) -> str:
    """接口时间可能是秒级/毫秒级时间戳（1793262474），也可能是已经是可读字符串，统一成字符串。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number > 1e11:  # 毫秒
        number /= 1000
    return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")


def title_key(title: str) -> str:
    """把标题归一化成去重用的键。

    国防部网会把同一篇稿子在多个栏目重复发布（/gfbw/qwfb/、/gfbw/xwfyr/…），
    检索结果里就会出现多条标题相同、URL 不同的记录 —— 按标题去重才是用户想要的"一篇一条"。
    """
    text = re.sub(r"[\s\u3000]+", "", title or "")
    return re.sub(r"[-—–]\s*(中华人民共和国国防部|中国军网|新华网|人民网|央视网)$", "", text)


def _same_site(url: str, base: str) -> bool:
    """判断两个 URL 是否属于同一站点（允许子域，且忽略 www.）。

    坑：人民网的列表页在 www.people.com.cn，而文章在 politics.people.com.cn，
    如果直接比较 netloc 相等，会把整站的文章全挡掉（本项目就踩过这个）。
    """
    a, b = urlparse(url).netloc.lower(), urlparse(base).netloc.lower()
    if not a or not b:
        return True
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _body_text(response: Any) -> str:
    body = response.body
    if isinstance(body, bytes):
        return body.decode(getattr(response, "encoding", None) or "utf-8", "replace")
    return body or ""


def _disp_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


class Throttle:
    """串行请求之间的最小间隔（同一进程共享一个实例）。"""

    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, delay)
        self._last = 0.0

    def wait(self) -> None:
        gap = time.perf_counter() - self._last
        if self._last and gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.perf_counter()


# ---------------------------------------------------------------- 抓取


def fetch(
    url: str, source: Dict[str, Any], *, timeout: float = 30.0, proxy: Optional[str] = None
) -> Tuple[int, str, str]:
    """返回 (状态码, 页面文本, 用的引擎)。

    engine=browser 时走无头隐身浏览器；engine=static 但失败时**自动降级一次浏览器**
    （政府站的 SSL 握手/反爬会偶发抽风，降级比直接报错实用）。
    """
    verify = source.get("verify", True)
    engine = source.get("engine", "static")
    extra = {} if verify else {"additional_args": {"ignore_https_errors": True}}

    def browser() -> Tuple[int, str, str]:
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            load_dom=True,
            block_ads=True,
            timeout=int(timeout * 1000),
            proxy=proxy,
            retries=1,
            **extra,
        )
        return int(page.status), _body_text(page), "browser"

    if engine == "browser":
        return browser()

    try:
        page = Fetcher.get(
            url, impersonate="chrome", stealthy_headers=True, timeout=timeout, retries=1, verify=verify, proxy=proxy
        )
    except Exception as exc:
        print(f"    （静态失败 {type(exc).__name__}，降级浏览器重试：{url}）")
        status, text, used = browser()
        return status, text, used + "(降级)"

    return int(page.status), _body_text(page), engine


# ---------------------------------------------------------------- 解析


def pick_text(page: Selector, selectors: str, limit_len: int = 20000) -> str:
    """按逗号分隔的选择器列表依次尝试，返回第一个非空结果的文本。"""
    for sel in [s.strip() for s in (selectors or "").split(",") if s.strip()]:
        found = page.css(sel)
        if found:
            text = _WS.sub(" ", " ".join(found[0].css("::text").getall())).strip()
            if len(text) > 20:
                return text[:limit_len]
    return ""


def auto_body(page: Selector, min_len: int = 200) -> Tuple[str, str]:
    """启发式找正文：文本够长、且占父节点比例高的最内层容器。返回 (文本, 选择器签名)。"""
    best_node, best_len, best_sig = None, 0, ""
    for node in page.css("div, article, section"):
        ident = node.attrib.get("id") or ""
        cls = node.attrib.get("class") or ""
        if _NOISE_CONTAINER.search(f"{ident} {cls}"):
            continue
        text = _WS.sub("", " ".join(node.css("::text").getall()))
        if len(text) < min_len:
            continue
        parent = node.parent
        parent_len = len(_WS.sub("", " ".join(parent.css("::text").getall()))) if parent is not None else len(text)
        if parent_len and len(text) / parent_len < 0.55:
            continue
        if len(text) > best_len:
            best_len = len(text)
            best_node = node
            best_sig = node.tag + (f"#{ident}" if ident else "") + ("." + ".".join(cls.split()[:2]) if cls else "")
    if best_node is None:
        return "", ""
    return _WS.sub(" ", " ".join(best_node.css("::text").getall())).strip()[:20000], best_sig


def parse_article(html: str, source: Dict[str, Any]) -> Dict[str, Any]:
    """抽标题、正文、发布时间。"""
    page = Selector(content=html)

    title = pick_text(page, source.get("title_selector", ""), limit_len=300)
    if not title:
        title = _WS.sub(" ", page.css("title::text").get() or "").strip()

    body = pick_text(page, source.get("body_selector", ""))
    body_selector_used = source.get("body_selector", "") or "auto"
    if not body:
        body, matched = auto_body(page)
        body_selector_used = f"auto:{matched}" if matched else "auto:failed"

    published = ""
    haystack = f"{title} {body[:400]}"
    page_text = _WS.sub(" ", " ".join(page.css("body ::text").getall()[:400]))
    for pattern in _DATE_PATTERNS:
        match = pattern.search(haystack) or pattern.search(page_text)
        if match:
            published = match.group(1).strip()
            break
    return {"title": title, "body": body, "published": published, "_body_selector_used": body_selector_used}


def extract_links(
    html: str, base_url: str, *, list_selector: str = "a", link_re: str = "", same_site: bool = True, min_label: int = 6
) -> List[Dict[str, str]]:
    """从 HTML 里抽 (标题, 绝对 URL) 候选，按 link_re 过滤、按 URL 去重。"""
    page = Selector(content=html)
    pattern = re.compile(link_re) if link_re else None
    seen: set = set()
    articles: List[Dict[str, str]] = []
    for anchor in page.css(list_selector or "a"):
        href = (anchor.attrib.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if pattern and not pattern.search(absolute):
            continue
        if same_site and not _same_site(absolute, base_url):
            continue
        label = _WS.sub(" ", " ".join(anchor.css("::text").getall())).strip()
        if len(label) < min_label or absolute in seen:
            continue
        seen.add(absolute)
        articles.append({"title": label, "url": absolute})
    return articles


def list_articles(html: str, source: Dict[str, Any], page_url: str = "") -> List[Dict[str, str]]:
    """列表页兜底路径：按源配置抽候选。

    基准 URL 必须是**本轮真正请求的那一页**（翻页时是 `index_2.html` 这种），相对链接才补得对；
    不传则退回源配置的 `url`（首页行为不变）。
    """
    return extract_links(
        html,
        page_url or source["url"],
        list_selector=source.get("list_selector", "a"),
        link_re=source.get("link_re", ""),
        same_site=True,
    )


def _page_template(template: str, source_url: str, page_no: int) -> Optional[str]:
    """把列表页 URL 套进分页模板，返回第 `page_no` 页的 URL；套不进去返回 None。

    模板有两种写法（都支持）：
    - 完整 URL：`https://www.ccg.gov.cn/wqzf/index_{page}.html` —— 直接替换 {page}，不再拼接；
    - 文件名：`index_{page}.html` —— 拼到源 url 的目录上（`…/wqzf/` + `index_2.html`；
      源 url 是文件时原地换掉文件名，`…/index.htm` + `index_2.htm`）。
    page_no=1 且模板没写 {page} 时用源 url 本身（首页就是列表首屏）。
    """
    if "{page}" not in template:
        return source_url if page_no == 1 else None
    if urlparse(template).scheme:
        return template.replace("{page}", str(page_no))
    parsed = urlparse(source_url)
    path = parsed.path
    if path.endswith("/"):
        candidate = path + template.replace("{page}", str(page_no))
    elif "." in path.rsplit("/", 1)[-1]:
        candidate = path.rsplit("/", 1)[0] + "/" + template.replace("{page}", str(page_no))
    else:
        return None
    return parsed._replace(path=candidate, params="", query="", fragment="").geturl()


def derive_page_url_template(source: Dict[str, Any], html: str, page_url: str) -> Optional[str]:
    """从首页 HTML 里推分页模板，**要求有实证**：返回 None 就只抓第 1 页。

    证据门槛（缺一不可，否则容易把文章链接的分页形似 URL 当成目录分页）：
    1. 至少 **2 条**同站链接归到同一个模板（href 里数字换 `{page}` 后完全相同）；
    2. 这些链接的页码里出现 `1` 或 `2`（分页第一页就是 1 或 2 起的 links）。

    反例（2026-11-20 实测）：nanhai 首页第一条带数字的文件名是**文章**链接
    `https://www.nanhai.org.cn/info-detail/21/18489.html`，只按单条链接推会得出
    `/info-detail/21/{page}.html` 这种伪模板，翻页翻出来全是 404/空页。所以还要：
    3. 页码取文件名里**最后一段**数字（不是 `info-detail/21/` 这种目录里的栏目 id），
       且文件名主干不能整个就是页码（`{page}.html` 不是分页，`index_{page}.html` 才是）。
    """
    page = Selector(content=html or "")
    groups: Dict[str, Dict[str, Any]] = {}
    for anchor in page.css("a"):
        href = (anchor.attrib.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = urljoin(page_url, href).split("#")[0]
        if not _same_site(absolute, page_url):
            continue
        name = urlparse(absolute).path.rsplit("/", 1)[-1]
        found = re.findall(r"\d+(?=\.[A-Za-z0-9]+$)", name)
        if not found:
            continue
        number = found[-1]
        template_name = name.replace(number, "{page}")
        if template_name == "{page}" + name[len(number) :]:
            continue
        if _page_template(template_name, source["url"], 2) is None:
            continue
        group = groups.setdefault(template_name, {"numbers": [], "hrefs": []})
        group["numbers"].append(number)
        group["hrefs"].append(href)

    for template_name, group in groups.items():
        numbers = set(group["numbers"])
        if len(numbers) < 2 or not ({"1", "2"} & numbers):
            continue
        example = urlparse(urljoin(page_url, group["hrefs"][0])).path
        head = example.rsplit("/", 1)[0] + "/"
        return f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}{head}{template_name}"
    return None


def _articles_since(html: str, source: Dict[str, Any], base_url: str, seen: set) -> List[Dict[str, str]]:
    """`list_articles` 的增量版本：只保留本轮新增的候选（翻页时按 URL 判重）。"""
    fresh: List[Dict[str, str]] = []
    for link in list_articles(html, source, base_url):
        if link["url"] not in seen:
            fresh.append(link)
    return fresh


def collect_list_pages(
    source: Dict[str, Any],
    args: argparse.Namespace,
    throttle: Throttle,
    proxy: Optional[str],
    list_pages: int,
    timeout: float,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """回退列表页：按 `page_url_template` 翻 `list_pages` 页，合并去重。

    **第 1 页也走模板**（配了 `page_url_template` 时），所以「第 1 页」= 模板渲染出的第 1 页
    （ccg_wqzf 就是 `index_1.html`），而不是无条件先用源 `url`；没配模板才先用源 `url` 抓第 1 页，
    再从它的 HTML 里推分页模板，推不出来就只抓这 1 页并在报告里注明未翻页。
    单页失败只记错误、不中断该源。
    """
    seen: set = set()
    collected: List[Dict[str, str]] = []
    status, html, engine = 0, "", ""
    pages_done = 0
    pages_attempted = 0
    errors: List[str] = []
    notes: List[str] = []
    template = str(source.get("page_url_template") or "").strip()
    page_activity: List[int] = []  # 每页新抽到的候选数（增量）

    first_url = source["url"]
    start_page = 1
    if template:
        rendered = _page_template(template, first_url, 1)
        if rendered:
            first_url = rendered
        else:
            notes.append(f"模板 {template} 套不进 {first_url}，退回抓源 url 本身且不翻页")
            template = ""
    status, html, engine = fetch(first_url, source, timeout=timeout, proxy=proxy)
    pages_attempted += 1
    first_page = list_articles(html, source, first_url)
    pages_done += 1
    for link in first_page:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        collected.append(link)
    page_activity.append(len(collected))

    if list_pages > 1 and not template:
        template = derive_page_url_template(source, html, first_url) or ""
    if not template:
        notes.append(
            f"列表翻页={pages_done} 页（未翻页：{'请求的列表页没有可推导的分页链接' if list_pages > 1 else 'list_pages=1'}）"
        )
        return collected, {
            "http_status": status,
            "engine": engine,
            "pages_done": pages_done,
            "pages_attempted": pages_attempted,
            "errors": errors,
            "notes": notes,
            "page_activity": page_activity,
        }

    for page_no in range(start_page + 1, list_pages + 1):
        page_url = _page_template(template, first_url, page_no)
        if not page_url:
            notes.append(f"模板 {template} 套不进 {first_url}，第 {page_no} 页起停止翻页")
            break
        throttle.wait()
        try:
            page_status, page_html, _engine = fetch(page_url, source, timeout=timeout, proxy=proxy)
        except Exception as exc:  # noqa: BLE001 - 单页失败只记错误，不中断该源
            errors.append(f"列表第 {page_no} 页失败 {page_url}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        pages_attempted += 1
        if page_status == 404:
            # 越过最后一页：正常收尾，不当错误（ccg_wqzf 第 24 页起就是 404）
            notes.append(f"第 {page_no} 页 404，判定已到最后一页，停止翻页")
            break
        if page_status != 200:
            errors.append(f"列表第 {page_no} 页 HTTP {page_status}（按空页处理）：{page_url}")
        fresh = _articles_since(page_html, source, page_url, seen)
        seen.update(link["url"] for link in fresh)
        collected.extend(fresh)
        pages_done += 1
        page_activity.append(len(fresh))
        if page_status == 200:
            status = page_status

    return collected, {
        "http_status": status,
        "engine": engine,
        "pages_done": pages_done,
        "pages_attempted": pages_attempted,
        "errors": errors,
        "notes": notes,
        "page_activity": page_activity,
    }


def extract_images(html: str, base_url: str, source: Dict[str, Any], limit: int = 3) -> List[str]:
    """取正文里前 limit 张图片的外链地址（能解析成绝对 http(s) 的那些，只存地址不下载）。

    先在正文容器（body_selector）里找，容器没命中才退回整页 —— 否则先命中的往往是站标/导航图标。
    """
    page = Selector(content=html)
    roots: List[Any] = []
    for sel in [s.strip() for s in (source.get("body_selector") or "").split(",") if s.strip()]:
        roots.extend(page.css(sel))
    if not roots:
        roots = [page]

    out: List[str] = []
    seen: set = set()
    for root in roots:
        for node in root.css("img"):
            src = (node.attrib.get("src") or node.attrib.get("data-src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            absolute = urljoin(base_url, src).split("#")[0]
            if not absolute.startswith(("http://", "https://")) or absolute in seen:
                continue
            seen.add(absolute)
            out.append(absolute)
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- 关键词


def fallback_filter_words(keywords: Dict[str, Any], source_key: str) -> List[str]:
    """回退列表页模式的过滤词表 = global（检索词兼过滤词）+ per_source（本源的补充词）。

    这是**回退路径专属**的口径：站内检索路径由服务端按 `global` 筛，只用 global 当检索词，
    per_source 不参与（站点检索接口没有"按源配词"的能力）。两个模式共用同一份 global，
    只有回退路径额外追加 per_source —— 报告里靠 `match_level=server` 与命中词来源区分。
    """
    return list(
        dict.fromkeys(list(keywords.get("global", [])) + list(keywords.get("per_source", {}).get(source_key, [])))
    )


def match_keywords(
    article: Dict[str, Any], keywords: Dict[str, Any], source_key: str, details: Optional[Dict[str, Any]] = None
) -> List[str]:
    """回退路径的本地过滤：返回命中的词（空列表 = 不保留，除非用 --no-filter 关掉过滤）。

    单独返回命中词而不是内部决定 keep，是因为 `--no-filter` 要"全留"、`match=all` 要
    "全部命中才留"，两种保留判定都在调用方，避免过滤函数与保留策略各说一套。
    details 非空时写回查了哪些词、命中哪些词、丢弃原因，供日志自证。
    """
    haystack = f"{article.get('title', '')} {article.get('body', '')}".lower()
    wanted = fallback_filter_words(keywords, source_key)
    hits = [word for word in wanted if word.lower() in haystack]

    for word in keywords.get("exclude", []):
        if word and word.lower() in haystack:
            if details is not None:
                details.update(
                    {
                        "reason": "命中排除词",
                        "exclude": word,
                        "hit": [],
                        "global_words": list(keywords.get("global", [])),
                        "per_source_words": list(keywords.get("per_source", {}).get(source_key, [])),
                    }
                )
            return []

    record: Dict[str, Any] = {
        "reason": "",
        "hit": hits,
        "global_words": list(keywords.get("global", [])),
        "per_source_words": list(keywords.get("per_source", {}).get(source_key, [])),
    }
    if not wanted:
        record["reason"] = "无可用过滤词"
    elif not hits:
        record["reason"] = "未命中过滤词"
    elif keywords.get("match") == "all" and len(hits) != len(wanted):
        record["reason"] = f"match=all 要求全部命中（命中 {len(hits)}/{len(wanted)}）"
    if details is not None:
        details.update(record)
    return hits


def match_level(title: str, body: str, hits: List[str]) -> str:
    """命中位置：title / content / title+content；没有命中词（--no-filter）记 none。"""
    if not hits:
        return "none"
    in_title = any(word.lower() in (title or "").lower() for word in hits)
    in_body = any(word.lower() in (body or "").lower() for word in hits)
    if in_title and in_body:
        return "title+content"
    return "title" if in_title else "content"


# ---------------------------------------------------------------- 站内检索


def resolve_pages(search: Dict[str, Any], args: argparse.Namespace, settings: RunSettings) -> int:
    """检索深度：命令行 --search-pages > 源配置 pages > settings.search_pages > 默认 5，上限 20。

    源配置优先于 settings 是刻意的：xinhua_mil 这类源把 `pages` 压到 1 是为了压住浏览器开销
    （见 sources.json 的 note），全局 settings 不该把它顶掉；settings 只给没写 `pages` 的源兜底。
    """
    pages = int(args.search_pages or search.get("pages") or settings.search_pages or DEFAULT_SEARCH_PAGES)
    return max(1, min(MAX_SEARCH_PAGES, pages))


def needs_proxy(source: Dict[str, Any]) -> bool:
    return bool(source.get("proxy") or (source.get("search") or {}).get("proxy"))


def resolve_proxy(
    source: Dict[str, Any], args: argparse.Namespace, global_proxy: Optional[str] = None
) -> Optional[str]:
    """只有配置里声明需要代理的源才用代理（命令行 --proxy > settings.proxy）；
    未提供时照常直连（让失败证据真实可见）。国内源永不带代理。"""
    if not needs_proxy(source):
        return None
    if global_proxy:
        return global_proxy
    print(f"    ⚠ {source['key']} 配置要求代理（search.proxy=true），但未传 --proxy/settings.proxy：本次直连，预期失败")
    return None


def _render(value: Any, keyword: str, page_no: int) -> Any:
    """渲染配置模板里的 {keyword}/{page}：整串就是一个占位符时保持原类型（page 转 int）。"""
    if isinstance(value, str):
        if value == "{page}":
            return int(page_no)
        if value == "{keyword}":
            return keyword
        return value.replace("{keyword}", keyword).replace("{page}", str(page_no))
    if isinstance(value, dict):
        return {k: _render(v, keyword, page_no) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, keyword, page_no) for v in value]
    return value


def _render_url(template: str, keyword: str, page_no: int) -> str:
    """URL 模板渲染：{keyword} 会 percent-encode，{page} 换成页码。"""
    return template.replace("{keyword}", quote(keyword)).replace("{page}", str(page_no))


def _passes_result_filter(url: str, search: Dict[str, Any]) -> bool:
    """检索后的二次过滤：频道型源（mod_qwfb / people_mil / xinhua_mil / xinhua_world）靠它收窄。"""
    link_re = search.get("link_re")
    if link_re and not re.search(link_re, url):
        return False
    prefixes = search.get("url_prefixes") or []
    return not prefixes or any(prefix in url for prefix in prefixes)


def _items_to_records(items: List[Any], search: Dict[str, Any], keyword: str) -> List[Dict[str, str]]:
    records = []
    for item in items:
        url = str(dig(item, search.get("url_field", "url"), "") or "").strip()
        title = strip_html(str(dig(item, search.get("title_field", "title"), "") or ""))
        if not url or not title or not _passes_result_filter(url, search):
            continue
        records.append(
            {
                "title": title,
                "url": url,
                "published": fmt_epoch(dig(item, search.get("date_field", ""), "")),
                "summary": strip_html(str(dig(item, search.get("summary_field", ""), "") or ""))[:400],
                "category": str(dig(item, search.get("category_field", ""), "") or ""),
                "search_keyword": keyword,
            }
        )
    return records


def _dedupe_into(
    collected: List[Dict[str, Any]],
    records: List[Dict[str, Any]],
    seen_urls: set,
    seen_titles: set,
    limit: int,
    guard: Optional[Dict[str, Any]] = None,
) -> int:
    """URL + 归一化标题双去重后并入 collected，返回本次新增条数。

    `limit` 是**检索收集上限**（--search-limit / settings.search_limit），0 = 不设上限。
    无论哪种情况，收集到 LIST_CANDIDATE_GUARD 条就置位 `guard["hit"]=True`（并停止本次并入），
    由调用方跳出翻页循环 —— 避免站内命中上万条的源把内存和耗时拉爆。
    """
    added = 0
    for record in records:
        key = title_key(record["title"])
        if record["url"] in seen_urls or (key and key in seen_titles):
            continue
        seen_urls.add(record["url"])
        if key:
            seen_titles.add(key)
        collected.append(record)
        added += 1
        if limit and len(collected) >= limit:
            break
        if len(collected) >= LIST_CANDIDATE_GUARD:
            if guard is not None:
                guard["hit"] = True
            break
    return added


def search_via_api(
    source: Dict[str, Any],
    keywords: List[str],
    pages: int,
    search_limit: int,
    proxy: Optional[str],
    throttle: Throttle,
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """api 型检索：GET/POST + query/JSON body + 自定义头，``{keyword}``/``{page}`` 占位。

    这里**只受 search_limit（收集上限）约束**，与"每源抓几篇详情"（fetch_limit）无关。
    """
    search = source["search"]
    method = str(search.get("method", "GET")).upper()
    url = search["url"]
    headers = search.get("headers") or None
    timeout = float(search.get("timeout", args.timeout))
    verify = bool(search.get("verify", True))
    page_start = int(search.get("page_start", 1))

    collected: List[Dict[str, Any]] = []
    seen_urls: set = set()
    seen_titles: set = set()
    totals: Dict[str, int] = {}
    status = 0
    guard = {"hit": False}
    for keyword in keywords:
        for page_no in range(page_start, page_start + pages):
            params = _render(search.get("params") or {}, keyword, page_no) or None
            body = _render(search.get("body") or {}, keyword, page_no)
            throttle.wait()
            if method == "POST":
                response = Fetcher.post(
                    url,
                    json=body or None,
                    headers=headers,
                    impersonate="chrome",
                    stealthy_headers=True,
                    timeout=timeout,
                    retries=1,
                    verify=verify,
                    proxy=proxy,
                )
            else:
                response = Fetcher.get(
                    url,
                    params=params,
                    headers=headers,
                    impersonate="chrome",
                    stealthy_headers=True,
                    timeout=timeout,
                    retries=1,
                    verify=verify,
                    proxy=proxy,
                )
            status = int(response.status)
            data = json.loads(_body_text(response) or "{}")
            total = dig(data, search.get("total_path", "data.total"), 0) or 0
            totals[keyword] = max(int(totals.get(keyword, 0)), int(total or 0))
            items = dig(data, search.get("items_path", "data.records"), []) or []
            if not items:
                break
            # 频道型源（mod_qwfb / people_mil）本页可能一条都不属于本频道，继续翻下一页
            _dedupe_into(
                collected, _items_to_records(items, search, keyword), seen_urls, seen_titles, search_limit, guard
            )
            if guard["hit"] or (search_limit and len(collected) >= search_limit):
                break
        if guard["hit"] or (search_limit and len(collected) >= search_limit):
            break

    meta = {"http_status": status, "total_by_keyword": totals, "method": method, "guard_hit": guard["hit"]}
    return collected, meta


def search_via_results_page(
    source: Dict[str, Any],
    keywords: List[str],
    pages: int,
    search_limit: int,
    proxy: Optional[str],
    throttle: Throttle,
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """results_page 型检索：结果就是一个可 GET 的页面，按 list_selector + link_re 抽链接。"""
    search = source["search"]
    template = search["url_template"]
    timeout = float(search.get("timeout", args.timeout))
    verify = bool(search.get("verify", True))

    collected: List[Dict[str, Any]] = []
    seen_urls: set = set()
    seen_titles: set = set()
    status = 0
    guard = {"hit": False}

    for keyword in keywords:
        # 结果页没有可用的翻页参数（调研未观察到），每个词固定只取第一页
        url = _render_url(template, keyword, 1)
        throttle.wait()
        response = Fetcher.get(
            url, impersonate="chrome", stealthy_headers=True, timeout=timeout, retries=1, verify=verify, proxy=proxy
        )
        status = int(response.status)
        links = extract_links(
            _body_text(response),
            url,
            list_selector=search.get("list_selector", "a"),
            link_re=search.get("link_re", ""),
            same_site=True,
        )
        records = [
            {
                "title": link["title"],
                "url": link["url"],
                "published": "",
                "summary": "",
                "category": "",
                "search_keyword": keyword,
            }
            for link in links
        ]
        _dedupe_into(collected, records, seen_urls, seen_titles, search_limit, guard)
        if guard["hit"] or (search_limit and len(collected) >= search_limit):
            break

    return collected, {"http_status": status, "total_by_keyword": {}, "method": "GET", "guard_hit": guard["hit"]}


def _build_page_action(spec: Optional[Dict[str, Any]], keyword: str) -> Optional[Callable[..., Any]]:
    """把配置里的 page_action 变成可调用对象（填框 + 回车），不需要就返回 None。"""
    if not spec:
        return None

    def action(page: Any) -> None:
        selector = spec.get("fill_selector") or "input"
        page.wait_for_selector(selector, timeout=int(spec.get("wait_ms", 8000)))
        page.fill(selector, keyword)
        page.keyboard.press(spec.get("press", "Enter"))
        page.wait_for_timeout(int(spec.get("after_ms", 2500)))

    return action


def _build_js_fetch_action(
    target_url: str, holder: Dict[str, Any], reuse_page: Optional[Callable[[], Any]] = None
) -> Callable[..., Any]:
    """在页面上下文里用 fetch() 调它自己的取数接口，结果塞进 holder。

    xinhua 系的取数接口静态直调恒 403（openresty），只有页面自身发起的请求才放行；
    而且它的检索是 hash 路由，改 hash 属于同文档导航，Playwright 拿不到新响应 —— 所以
    每页都在一个已打开的基础页里 fetch 目标 URL。

    `reuse_page` 给的是"复用上一次那个页面"的取值器（拿不到活页面就抛异常，由调用方重新导航）；
    不传就只认识 goto 之后传进来的 page。用的页面记在 holder["page"] 里，供下一次复用。
    """

    def action(page: Any = None) -> None:
        target = page if page is not None else (reuse_page() if reuse_page else None)
        if target is None:
            raise RuntimeError("没有可用页面：既没传 page，也没有可复用的活页面")
        holder["page"] = target
        holder["result"] = target.evaluate(
            """async (u) => {
                const r = await fetch(u, {headers: {"Accept": "application/json, text/plain, */*"}});
                return {status: r.status, body: await r.text()};
            }""",
            target_url,
        )

    return action


def _latest_json(session: Any, xhr_re: str) -> Optional[Any]:
    """取最近一条命中 xhr_re 的 XHR 响应并解析成 JSON。

    倒着找是因为最后一条才是当前页的结果；中间可能有空 body/被中断的请求，解析不了就跳过。
    """
    for response in reversed(session.network.search(url_pattern=xhr_re, include_static=False, limit=None)):
        text = _body_text(response).strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def search_via_browser(
    source: Dict[str, Any],
    keywords: List[str],
    pages: int,
    search_limit: int,
    proxy: Optional[str],
    throttle: Throttle,
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """browser 型检索：用隐身浏览器打开检索页，结果从页面自身发出的 XHR 里取（静态直调 403）。

    也支持从渲染后的 DOM 抽链接（配置 dom_link_selector 时）。
    """
    search = source["search"]
    template = search.get("url_template") or source["url"]
    xhr_re = search.get("xhr_url_re") or ""
    js_fetch = search.get("js_fetch") or {}
    page_start = int(search.get("page_start", 1))

    collected: List[Dict[str, Any]] = []
    seen_urls: set = set()
    seen_titles: set = set()
    totals: Dict[str, int] = {}
    errors: List[str] = []
    status = 0
    guard = {"hit": False}

    session_kwargs: Dict[str, Any] = dict(
        headless=True,
        network_idle=True,
        load_dom=True,
        block_ads=True,
        timeout=int(args.timeout * 1000),
        proxy=proxy,
        retries=1,
        record_requests=bool(xhr_re),
    )
    if not search.get("verify", True):
        session_kwargs["additional_args"] = {"ignore_https_errors": True}

    with StealthySession(**session_kwargs) as session:
        base_url = _render_url(template, keywords[0] if keywords else "", page_start) if keywords else template
        js_fetch_page: List[Any] = []  # 上一次 js_fetch 用过的活页面，供后续词/页复用

        def reuse_page() -> Any:
            """复用上一次的检索页；页面被关掉/崩掉就抛异常，由调用方重新导航。"""
            page = js_fetch_page[-1] if js_fetch_page else None
            if page is None or page.is_closed():
                raise RuntimeError("上一次检索用的页面已经不在了")
            page.evaluate("1")  # 探活：页面上下文没了会在这里抛出来
            return page

        for keyword in keywords:
            for page_no in range(page_start, page_start + pages):
                throttle.wait()
                url = _render_url(template, keyword, page_no)
                holder: Dict[str, Any] = {}
                action: Optional[Callable[..., Any]]
                if js_fetch:
                    fetch_url = _render_url(js_fetch["url_template"], keyword, page_no)
                    if js_fetch_page and url == base_url:
                        # 检索页固定、只有 curPage 变时不必重新导航：实证 4 次导航 22~26s，
                        # 改成"一次导航 + 页内 4 次 fetch" 只要 6.0s，取回的每页 10 条与页码
                        # 顺序都不变（measure_xinhua_reuse.json）。
                        action = _build_js_fetch_action(fetch_url, holder, reuse_page=reuse_page)
                        try:
                            response = session.fetch(url, page_action=action)
                        except Exception as exc:
                            # 复用的页面失效了：重新导航一次再试，别把整条检索路线报废
                            errors.append(f"复用检索页失败，重新导航 {url}: {type(exc).__name__}: {str(exc)[:80]}")
                            throttle.wait()
                            action = _build_js_fetch_action(fetch_url, holder, reuse_page=reuse_page)
                            response = session.fetch(url, page_action=action)
                    else:
                        action = _build_js_fetch_action(fetch_url, holder)
                        response = session.fetch(url, page_action=action)
                    js_fetch_page[:] = [holder.get("page")]
                else:
                    action = _build_page_action(search.get("page_action"), keyword)
                    try:
                        response = session.fetch(url, page_action=action) if action else session.fetch(url)
                    except Exception as exc:
                        # SPA 的 hash 翻页偶发打不开：记下来，换下一个词/页，别让整条检索路线报废
                        errors.append(f"检索页打开失败 {url}: {type(exc).__name__}: {str(exc)[:100]}")
                        break
                status = int(response.status)

                items: Optional[List[Any]] = None
                if js_fetch:
                    result = holder.get("result") or {}
                    body = str(result.get("body") or "")
                    if int(result.get("status") or 0) == 200 and body:
                        data = json.loads(body)
                        items = dig(data, search.get("items_path", "content.results"), []) or []
                        total = dig(data, search.get("total_path", ""), 0) or 0
                        totals[keyword] = max(int(totals.get(keyword, 0)), int(total or 0))
                    else:
                        errors.append(
                            f"站内取数接口异常：status={result.get('status')} "
                            f"body={body[:80]!r}（403/503 多为风控限流：调大 --delay 或分次跑该源）"
                        )
                elif xhr_re:
                    data = _latest_json(session, xhr_re)
                    if data is not None:
                        items = dig(data, search.get("items_path", "content.results"), []) or []
                        total = dig(data, search.get("total_path", ""), 0) or 0
                        totals[keyword] = max(int(totals.get(keyword, 0)), int(total or 0))
                if items is None and search.get("dom_link_selector"):
                    links = extract_links(
                        _body_text(response),
                        url,
                        list_selector=search["dom_link_selector"],
                        link_re=search.get("link_re", ""),
                        same_site=True,
                    )
                    items = [{"title": link["title"], "url": link["url"]} for link in links]
                if items is None:
                    break

                records = _items_to_records(items, search, keyword)
                _dedupe_into(collected, records, seen_urls, seen_titles, search_limit, guard)
                if guard["hit"] or (search_limit and len(collected) >= search_limit):
                    break
            if guard["hit"] or (search_limit and len(collected) >= search_limit):
                break

    return collected, {
        "http_status": status,
        "total_by_keyword": totals,
        "method": "browser",
        "errors": errors,
        "guard_hit": guard["hit"],
    }


def collect_candidates(
    source: Dict[str, Any],
    keywords: List[str],
    search_limit: int,
    args: argparse.Namespace,
    throttle: Throttle,
    proxy: Optional[str],
    list_pages: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
    settings: Optional[RunSettings] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """取候选：站内检索优先，失败/为空/无检索时回退列表页（并可翻页）。返回 (候选, 路线说明)。

    `search_limit` 只管**收集**多少候选；抓多少篇详情由调用方按 fetch_limit 截取，两者互不影响。
    """
    search = source.get("search") or {}
    search_type = str(search.get("type") or "none")
    pages = resolve_pages(search, args, settings or RunSettings())
    errors: List[str] = []
    notes: List[str] = []

    meta: Dict[str, Any] = {
        "search_type": search_type,
        "pages": pages,
        "search_hits": 0,
        "list_candidates": 0,
        "collected": 0,
        "guard_hit": False,
        "total_by_keyword": {},
        "http_status": 0,
        "fallback": False,
        "route": "none",
        "label": "无站内检索",
        "list_engine": "",
        "list_pages_done": 0,
        "list_pages_requested": list_pages,
        "list_page_activity": [],
        "errors": errors,
        "notes": notes,
    }

    can_search = search_type in ("api", "results_page", "browser")
    if can_search and search.get("unavailable"):
        # 调研已确认取不到结果：配置留档，运行时直接回退（不算运行错误，只在报告里注明原因）
        notes.append(f"站内检索不可用：{search.get('unavailable_reason', '')}")
        meta["label"] = f"{search_type}(已知不可用)"
        can_search = False
    elif can_search:
        meta["label"] = search_type if search_type != "api" else "api"
    if can_search:
        try:
            runner = {"api": search_via_api, "results_page": search_via_results_page, "browser": search_via_browser}[
                search_type
            ]
            items, sub = runner(source, keywords, pages, search_limit, proxy, throttle, args)
            meta["http_status"] = sub.get("http_status", 0)
            meta["total_by_keyword"] = sub.get("total_by_keyword", {})
            meta["search_hits"] = len(items)
            meta["collected"] = len(items)
            meta["guard_hit"] = bool(sub.get("guard_hit"))
            errors.extend(sub.get("errors", []))
            if meta["guard_hit"]:
                # 护栏触发不是错误：站内命中量级本身超过我们愿意承担的范围，收下已拿到的候选
                notes.append(
                    f"⚠ 单源候选已达护栏 {LIST_CANDIDATE_GUARD} 条：打印提醒并停止继续翻页"
                    f"（--search-limit 0 时也有这条护栏）"
                )
            if search_type == "api":
                meta["label"] = f"api({sub.get('method', 'GET')})"
            if items:
                meta["route"] = "search"
                return items, meta
            errors.append(f"站内检索({search_type})取回 0 条")
        except Exception as exc:
            errors.append(f"站内检索({search_type})失败 {type(exc).__name__}: {str(exc)[:160]}")

    # 回退列表页：站内检索不可用、失败、或 0 条
    meta["fallback"] = True
    try:
        candidates, list_meta = collect_list_pages(source, args, throttle, proxy, list_pages, timeout)
        meta["http_status"] = list_meta["http_status"]
        meta["list_engine"] = list_meta["engine"]
        meta["list_candidates"] = len({item["url"] for item in candidates})
        meta["collected"] = meta["list_candidates"]
        meta["list_pages_done"] = list_meta["pages_done"]
        meta["list_page_activity"] = list_meta["page_activity"]
        errors.extend(list_meta["errors"])
        notes.extend(list_meta["notes"])
        meta["route"] = "list"
        meta["label"] = f"{meta['label']}→回退列表页"
        return candidates, meta
    except Exception as exc:
        errors.append(f"列表页失败 {type(exc).__name__}: {str(exc)[:160]}")
        meta["label"] = f"{meta['label']}→列表页也失败"
        return [], meta


# ---------------------------------------------------------------- 入库（db.py 延迟导入）

DB_NOT_READY = "db.py 尚未就绪（gov_news/db.py 不存在或导入失败）"


def load_db_module() -> Tuple[Optional[Any], str]:
    """延迟导入 db.py：它由另一张卡并行实现，缺席时其余功能仍要能独立跑通。"""
    try:
        import db  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 - 报告原因而不是吞掉
        return None, f"{DB_NOT_READY}：{type(exc).__name__}: {exc}"
    return db, ""


def load_records_from_json(module: Any, conn: Any, path: Path, batch_time: str) -> Any:
    """调 db.load_records_from_json；它的签名以 db.py 为准，两种常见形态都试一次。"""
    try:
        return module.load_records_from_json(conn, str(path), batch_time=batch_time)
    except TypeError:
        return module.load_records_from_json(conn, str(path))


def close_conn(conn: Any) -> None:
    closer = getattr(conn, "close", None)
    if callable(closer):
        closer()


# ---------------------------------------------------------------- 报告


def hit_origin(word: str, global_words: List[str]) -> str:
    """命中词来自哪张表：global（检索词兼过滤词）/ per_source（回退时追加的补充词）。"""
    return "global" if word in global_words else "per_source"


def format_hits(hits: List[str], match_info: Dict[str, Any], limit: int = 6) -> str:
    """命中词如实打印，并标出来源，便于区分"按检索词留的"和"按 per_source 补的词留的"。"""
    if not hits:
        return "[]"
    global_words = list(match_info.get("global_words") or [])
    shown = [f"{word}({hit_origin(word, global_words)})" for word in hits[:limit]]
    if len(hits) > limit:
        shown.append(f"…另{len(hits) - limit}个")
    return "[" + ", ".join(shown) + "]"


def format_words(words: List[str], limit: int = 12) -> str:
    """把词表打印成人能一眼扫完的形式：先按顺序去重（global 与 per_source 常重复），超长截断。"""
    if not words:
        return "[]"
    unique = list(dict.fromkeys(words))
    shown = unique[:limit]
    if len(unique) > limit:
        shown.append(f"…另{len(unique) - limit}个")
    return "[" + ", ".join(shown) + "]"


def fallback_log_line(
    name: str, record: Dict[str, Any], match_info: Dict[str, Any], keep: bool, no_filter: bool
) -> str:
    """回退路径的单条处理日志：保留/丢弃 + 依据（命中哪些词、丢了为什么）。

    关键点是这句必须自证：`--no-filter` 全留时不能出现"丢弃"字样，
    丢弃时不能只说"未命中"而不说是按哪几个词判的（词表本身在启动时已整份打印）。
    """
    title = record["title"][:36]
    checked = list(match_info.get("global_words") or []) + list(match_info.get("per_source_words") or [])
    hits = list(match_info.get("hit") or [])
    reason = match_info.get("reason") or ""

    if keep:
        if no_filter and not hits:
            return f"    ✓ [{name}] {title} 保留（--no-filter 未做过滤，查看词={format_words(checked)}）"
        return f"    ✓ [{name}] {title} 保留 命中={format_hits(hits, match_info)}"
    if reason == "命中排除词":
        return f"    - [{name}] {title} 丢弃原因=命中排除词({match_info.get('exclude')}) 查看词={format_words(checked)}"
    return (
        f"    - [{name}] {title} 丢弃原因={reason or '未命中过滤词'}"
        f" 查看词={format_words(checked)} 命中={format_hits(hits, match_info)}"
    )


def status_of(meta: Dict[str, Any], errors: List[str]) -> str:
    """每源状态：失败 / 回退列表页 / 检索无结果 / 部分成功 / 成功。

    「检索无结果」专门表示**检索这条路通了但 0 条**（fmprc/cctv 这类频道检索取不到结果的源），
    与「检索报错后回退」「详情抓取有错」分开计；它没有回退，所以不会和「回退列表页」混。
    """
    if meta["route"] == "none":
        return "失败"
    if meta["fallback"]:
        return "回退列表页"
    if not meta["search_hits"]:
        return "检索无结果"
    if errors:
        return "部分成功"
    return "成功"


def print_source_report(stat: Dict[str, Any]) -> None:
    print(f"\n[{stat['name']}/{stat['key']}] 检索方式={stat['label']} 状态={stat['status']}")
    print(
        f"    站内总数={stat['total_label']}  收集候选={stat['collected']}  详情抓取={stat['fetched']}  "
        f"入库={stat['stored_label']}  错误={len(stat['errors'])}  耗时 {stat['elapsed']:.1f}s"
    )
    if stat["detail"]:
        print(f"    {stat['detail']}")
    for note in stat["notes"][:2]:
        print(f"    · {note}")
    for err in stat["errors"][:3]:
        print(f"    ! {err}")
    if len(stat["errors"]) > 3:
        print(f"    ! …另有 {len(stat['errors']) - 3} 条错误未列出")


def print_summary(
    stats: List[Dict[str, Any]],
    elapsed: float,
    batch_time: str,
    total_kept: int,
    dump_path: Optional[Path],
    list_only: bool,
    candidate_total: int = 0,
) -> None:
    print(f"\n===== 汇总（{elapsed}s，批次 {batch_time}）=====")
    header = (
        _pad("源", 24)
        + _pad("检索方式", 34)
        + _pad("状态", 12)
        + _pad("站内总数", 22)
        + _pad("收集候选", 10)
        + _pad("详情抓取", 10)
        + _pad("入库", 12)
        + _pad("错误", 6)
        + "耗时"
    )
    print(header)
    print("-" * _disp_width(header))
    for stat in stats:
        print(
            _pad(stat["name"], 24)
            + _pad(stat["label"], 34)
            + _pad(stat["status"], 12)
            + _pad(stat["total_label"], 22)
            + _pad(str(stat["collected"]), 10)
            + _pad(str(stat["fetched"]), 10)
            + _pad(stat["stored_label"], 12)
            + _pad(str(len(stat["errors"])), 6)
            + f"{stat['elapsed']:.1f}s"
        )
    failed = [s for s in stats if s["status"] == "失败"]
    no_result = [s for s in stats if s["status"] == "检索无结果"]
    kept_text = (
        f"共列出候选 {candidate_total} 篇（--list-only，未抓详情/未入库）" if list_only else f"共保留 {total_kept} 篇"
    )
    print(f"\n{kept_text}；失败源 {len(failed)} 个{'：' + ', '.join(s['key'] for s in failed) if failed else ''}")
    if no_result:
        print(f"检索通但 0 条（未回退）{len(no_result)} 个：{', '.join(s['key'] for s in no_result)}")
    if dump_path:
        print(f"结果已导出 -> {dump_path}")


# ---------------------------------------------------------------- 主流程


def run_from_json(args: argparse.Namespace, db_params: Optional[Dict[str, Any]] = None) -> int:
    """--from-json：把已有 JSON 回灌入库（这条路径只做入库，不抓取）。"""
    path = Path(args.from_json)
    if not path.exists():
        print(f"❌ 找不到 JSON 文件：{path}")
        return 2
    module, err = load_db_module()
    if module is None:
        print(f"❌ 无法回灌：{err}")
        return 2
    batch_time = datetime.now().isoformat(timespec="seconds")
    params = dict(db_params or {})
    print(f"回灌 {path} -> db_name={params.get('db_name', DEFAULT_DB_NAME)} batch_time={batch_time}")
    try:
        conn = module.connect(**params)
    except Exception as exc:
        print(f"❌ 无法回灌：连接数据库失败 {type(exc).__name__}: {exc}")
        return 2
    try:
        result = load_records_from_json(module, conn, path, batch_time)
    finally:
        close_conn(conn)
    print(f"回灌结果：{result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按配置采集官网/涉海机构新闻：站内检索优先、列表页兜底 → 关键词过滤 → 写 MySQL（每源一表）"
    )
    parser.add_argument("--config", default=str(HERE / "sources.json"))
    parser.add_argument("--keywords", default=str(HERE / "keywords.json"))
    parser.add_argument(
        "--settings",
        default=str(SETTINGS_PATH),
        help=f"每轮运行的全局参数文件（默认 {SETTINGS_PATH.name}；不存在则全部走源配置/内置默认）",
    )
    parser.add_argument("--sources", default="", help="只跑指定源（key 逗号分隔），默认全部启用的源")
    parser.add_argument(
        "--limit",
        "--fetch-limit",
        dest="fetch_limit",
        type=int,
        default=0,
        help=(
            "每源抓多少篇详情（0=用 settings/源配置）。--limit 是它的向后兼容别名；"
            "本参数**不再截断候选收集**（那是 --search-limit 的事）"
        ),
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=0,
        help=(
            f"每源检索阶段最多收集多少候选。不传（或传 0）=用 settings.search_limit；"
            f"要把某轮设成不设上限，只能在 settings 里把 search_limit 写成 0/负数（仍受 "
            f"{LIST_CANDIDATE_GUARD} 条护栏约束）"
        ),
    )
    parser.add_argument(
        "--list-pages",
        type=int,
        default=0,
        help=f"回退列表页最多翻几页（0=用 settings/源配置；上限 {MAX_LIST_PAGES}）",
    )
    parser.add_argument(
        "--keyword", default="", help="临时覆盖本次的检索词，同时替换 keywords.json 的 global 过滤词（逗号分隔）"
    )
    parser.add_argument(
        "--no-filter", action="store_true", help="回退列表页时不做关键词过滤，全部保留（检索路径本来就不本地筛）"
    )
    parser.add_argument("--list-only", action="store_true", help="只取候选列表，不抓详情页、不入库")
    parser.add_argument(
        "--search-pages", type=int, default=0, help=f"每个检索词翻几页（0=用源配置，上限 {MAX_SEARCH_PAGES}）"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help=f"请求间隔秒数（≥1；0=用 settings.delay_seconds，内置 {DEFAULT_DELAY}）",
    )
    parser.add_argument(
        "--proxy",
        default="",
        help="代理地址，只对配置里 search.proxy=true 的源生效（传空则用 settings.proxy，同样只给这些源）",
    )
    parser.add_argument("--no-db", action="store_true", help="不写库（只做检索/报告，便于离线验证）")
    parser.add_argument("--dump-records", default="", help="把本轮结果导出到指定 JSON 路径（默认不导出）")
    parser.add_argument("--from-json", default="", help="不抓取，直接把指定 JSON 回灌入库")
    parser.add_argument(
        "--db-name", default="", help=f"MySQL 库名（默认用 settings.json 的 db.name，其次 {DEFAULT_DB_NAME}）"
    )
    parser.add_argument(
        "--db-password",
        default="",
        help="MySQL 密码（默认取 GOV_NEWS_DB_PASSWORD 环境变量，其次 db.py 的默认值；不写进 settings.json）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help=f"单个请求超时秒数（0=用 settings.timeout_seconds，内置 {DEFAULT_TIMEOUT}）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印每个请求的 INFO 日志（默认只留报告）")
    args = parser.parse_args()

    logging.getLogger("scrapling").setLevel(logging.INFO if args.verbose else logging.WARNING)

    settings_path = Path(args.settings)
    settings, settings_note = load_settings(settings_path)
    if not settings_path.exists():
        print(f"⚠ 未找到 settings 文件：{settings_note}")
    elif "解析失败" in settings_note:
        # 文件在但坏了：绝不能静默降级成内置默认值，否则用户以为参数生效了
        print(f"⚠ settings 文件存在但读不了：{settings_note}")
        print("⚠ 本轮全部走源配置/内置默认；请修好该文件或换 --settings 指向另一份")
    if args.search_pages and not 1 <= args.search_pages <= MAX_SEARCH_PAGES:
        parser.error(f"--search-pages 取值范围 1~{MAX_SEARCH_PAGES}")
    if args.list_pages and not 1 <= args.list_pages <= MAX_LIST_PAGES:
        parser.error(f"--list-pages 取值范围 1~{MAX_LIST_PAGES}")
    if args.search_limit < 0 and not (settings.search_limit and settings.search_limit < 0):
        parser.error("--search-limit 只接受 0（用 settings）或正数；'不设上限'请把 settings.search_limit 设为 0/负数")
    delay = resolve_delay(args, settings)
    timeout = resolve_timeout(args, settings)
    search_limit = resolve_search_limit(args, settings)
    if delay < 1:
        parser.error(f"--delay（或 settings.delay_seconds={settings.delay_seconds}）必须 ≥ 1（对国内站点的礼貌间隔）")
    if timeout <= 0:
        parser.error(f"--timeout（或 settings.timeout_seconds={settings.timeout_seconds}）必须 > 0")
    db_params = resolve_db_params(args, settings)
    if args.from_json:
        return run_from_json(args, db_params)

    config = load_config(Path(args.config))
    keywords = load_keywords(args)
    sources = select_sources(config, args.sources)
    if not sources:
        print("❌ 没有可跑的源（检查 --sources 与配置里的 enabled）")
        return 2

    db_module: Optional[Any] = None
    db_note = ""
    if args.no_db:
        db_note = "跳过（--no-db）"
    else:
        db_module, db_err = load_db_module()
        db_note = f"已就绪（库 {db_params['db_name']}）" if db_module else f"未就绪：{db_err}"
    stored_label_none = "0(未就绪)" if not args.no_db and not db_module else "0(未入库)"

    batch_time = datetime.now().isoformat(timespec="seconds")
    throttle = Throttle(delay)
    global_proxy = resolve_global_proxy(args, settings, str(settings_path))
    print(f"配置：{args.config}")
    print(f"设置：{settings_note}")
    keyword_source = "命令 --keyword 覆盖" if args.keyword else "keywords.json global"
    print(f"检索词兼过滤词（{keyword_source}）：{keywords['global']}")
    print(
        f"         per_source（只在回退列表页时追加到过滤词，不改检索词）="
        f"{ {k: v for k, v in keywords['per_source'].items() if k in [s['key'] for s in sources]} }"
    )
    print(f"         exclude={keywords['exclude']}  match={keywords['match']}")
    search_limit_text = (
        f"{search_limit}（不设上限，仅受 {LIST_CANDIDATE_GUARD} 条护栏约束）"
        if search_limit <= 0
        else str(search_limit)
    )
    print(
        f"检索深度：每词默认 {DEFAULT_SEARCH_PAGES} 页（源可覆盖，上限 {MAX_SEARCH_PAGES}）；"
        f"收集上限 search-limit={search_limit_text}；请求间隔 {delay}s；超时 {timeout}s"
    )
    print(
        f"详情抓取：fetch-limit 默认 {settings.fetch_limit}（源配置/source_overrides 可覆盖，命令行可覆盖）；"
        f"列表页默认翻 {settings.list_pages} 页"
    )
    print(f"代理：{'已提供（仅 search.proxy=true 的源使用）' if global_proxy else '未提供'}")
    print(f"入库：{db_note}")
    print(f"批次时间：{batch_time}")
    print(f"待抓 {len(sources)} 个源：{', '.join(s['name'] for s in sources)}")

    started = time.perf_counter()
    all_records: List[Dict[str, Any]] = []
    stats: List[Dict[str, Any]] = []
    conn = None
    if db_module is not None:
        try:
            conn = db_module.connect(**db_params)
        except Exception as exc:
            db_note = f"连接失败：{type(exc).__name__}: {exc}（本轮跳过入库）"
            stored_label_none = "0(连接失败)"
            print(f"⚠ 入库：{db_note}")
            db_module, conn = None, None

    try:
        for source in sources:
            fetch_limit = resolve_fetch_limit(source, args, settings)
            list_pages = resolve_list_pages(source, args, settings)
            stat: Dict[str, Any] = {
                "key": source["key"],
                "name": source["name"],
                "label": "无站内检索",
                "status": "失败",
                "search_hits": 0,
                "list_candidates": 0,
                "collected": 0,
                "total_label": "-",
                "list_pages": 0,
                "fetched": 0,
                "stored": 0,
                "stored_label": stored_label_none,
                "saved": 0,
                "skipped": 0,
                "detail": "",
                "errors": [],
                "notes": [],
            }
            t0 = time.perf_counter()
            proxy = resolve_proxy(source, args, global_proxy)
            candidates, meta = collect_candidates(
                source, keywords["global"], search_limit, args, throttle, proxy, list_pages, timeout, settings
            )
            stat["label"] = meta["label"]
            stat["search_hits"] = meta["search_hits"]
            stat["list_candidates"] = meta["list_candidates"]
            stat["collected"] = meta["collected"]
            stat["list_pages"] = meta["list_pages_done"]
            stat["errors"] = list(meta["errors"])
            stat["notes"] = list(meta["notes"])
            # 状态要在这里先算一次：--list-only 不抓详情，末尾那次计算不会执行，
            # 不先算就会把还没覆盖的初始值 "失败" 打进报告
            stat["status"] = status_of(meta, stat["errors"])

            totals = meta["total_by_keyword"]
            if totals:
                stat["total_label"] = " ".join(f"{k}:{v}" for k, v in totals.items())
            elif meta["fallback"]:
                # 回退源的"站内总数"没有服务端数字，这里如实标成列表候选口径
                stat["total_label"] = f"列表(未检索)={meta['list_candidates']}"

            detail_bits = []
            if meta["search_type"] != "none":
                detail_bits.append(f"检索={meta['search_type']} 页数={meta['pages']}")
            if totals:
                detail_bits.append("站内总数=" + " ".join(f"{k}:{v}" for k, v in totals.items()))
            detail_bits.append(f"收集候选={meta['collected']}")
            if meta["fallback"]:
                detail_bits.append(
                    f"列表翻页={meta['list_pages_done']} 页（请求最多 {meta['list_pages_requested']} 页 / 候选 "
                    f"{meta['list_candidates']} 条）列表引擎={meta['list_engine'] or '-'}"
                )
                # 回退路径的过滤词在启动横幅里只打了本批源的字面量，这里给出该源实际用的那份（去重后）
                detail_bits.append(f"过滤词={format_words(fallback_filter_words(keywords, source['key']))}")
            detail_bits.append(f"HTTP={meta['http_status']}")
            stat["detail"] = "｜".join(detail_bits)

            source_records: List[Dict[str, Any]] = []
            picked = candidates[:fetch_limit] if fetch_limit else []
            if args.list_only:
                print(
                    f"\n[{source['name']}/{source['key']}] 检索方式={stat['label']} 状态={stat['status']}\n"
                    f"    站内总数={stat['total_label']}  收集候选={meta['collected']}  "
                    f"列表展示前 {len(picked)} 条（--list-only）\n"
                    f"    {stat['detail']}"
                )
                for note in stat["notes"][:3]:
                    print(f"    · {note}")
                for err in stat["errors"][:3]:
                    print(f"    ! {err}")
                for idx, item in enumerate(picked, 1):
                    print(f"    {idx}. {item['title'][:40]} -> {item['url'][:80]}")
            else:
                # 详情页单独一套引擎：检索源(api)的详情页仍是普通网页，用 detail_engine 抓
                detail_source = {
                    **source,
                    "engine": source.get("detail_engine", "static"),
                    "verify": source.get("detail_verify", source.get("verify", True)),
                }

                match_info: Dict[str, Any] = {}
                for item in picked:
                    try:
                        throttle.wait()
                        detail_status, detail_html, _ = fetch(item["url"], detail_source, timeout=timeout, proxy=proxy)
                        parsed = parse_article(detail_html, source)
                        images = extract_images(detail_html, item["url"], source, limit=3)
                        title = strip_html(parsed["title"] or item["title"])
                        body = parsed["body"]
                        record = {
                            "source": source["key"],
                            "source_name": source["name"],
                            "url": item["url"],
                            "list_title": item["title"],
                            "title": title,
                            "body": body,
                            "body_length": len(body),
                            # 检索接口给的发布时间比页面正则更准，优先用
                            "published": item.get("published") or parsed["published"],
                            "summary": strip_html(item.get("summary", ""))[:400],
                            "category": item.get("category", ""),
                            "search_keyword": item.get("search_keyword", ""),
                            "image_urls": images,
                            "http_status": detail_status,
                            "fetched_at": datetime.now().isoformat(timespec="seconds"),
                            "_body_source": parsed["_body_selector_used"],
                        }
                        stat["fetched"] += 1
                        # 太短的正文多半是"记者会索引页/图片快讯"，可用 min_body_length 丢掉（不算错误）
                        if record["body_length"] < source.get("min_body_length", 0):
                            stat["skipped"] += 1
                            print(
                                f"    ~ [{source['name']}] {record['title'][:34]} "
                                f"正文仅{record['body_length']}字，按 min_body_length 丢弃"
                            )
                            continue
                        if meta["route"] == "search":
                            # 站内检索是服务端按检索词（global）筛的，不再本地筛一遍
                            record["matched_keywords"] = [record["search_keyword"]] if record["search_keyword"] else []
                            record["match_level"] = "server"
                            keep = True
                        else:
                            # 回退路径：过滤词表 = global（检索词兼过滤词）+ per_source（本源补充词）
                            match_info: Dict[str, Any] = {}
                            hits = match_keywords(record, keywords, source["key"], match_info)
                            record["matched_keywords"] = hits
                            record["match_level"] = match_level(record["title"], record["body"], hits)
                            # --no-filter 是"不做过滤、全留"，与"过滤后命中"是两回事：
                            # 前者 hits 可能为空但 keep=True，所以日志必须按 keep 而不是按 hits 措辞
                            keep = args.no_filter or (bool(hits) and not match_info.get("reason"))
                        if keep:
                            source_records.append(record)
                            stat["saved"] += 1
                            if meta["route"] == "search":
                                # 检索源记服务端用的那个 global 检索词，和回退路径的命中词来源分开打印
                                print(
                                    f"    ✓ [{source['name']}] {record['title'][:36]} 保留 "
                                    f"检索词={record['search_keyword'] or '-'}(global，服务端筛)"
                                )
                            else:
                                print(fallback_log_line(source["name"], record, match_info, True, args.no_filter))
                        elif meta["route"] != "search":
                            print(fallback_log_line(source["name"], record, match_info, False, args.no_filter))
                    except Exception as exc:
                        stat["errors"].append(f"{item['url']}: {type(exc).__name__}: {str(exc)[:100]}")

            if not args.list_only and source_records:
                all_records.extend(source_records)
                if db_module is not None:
                    try:
                        db_module.ensure_schema(conn, source["key"], source["name"])
                        result = db_module.upsert_records(
                            conn, source["key"], source["name"], source_records, batch_time
                        )
                        inserted = int(result.get("inserted", 0))
                        updated = int(result.get("updated", 0))
                        stat["stored"] = inserted + updated
                        stat["stored_label"] = str(stat["stored"])
                        if result.get("skipped") or result.get("errors"):
                            stat["detail"] += f"｜入库明细={result}"
                    except Exception as exc:
                        stat["errors"].append(f"入库失败 {type(exc).__name__}: {str(exc)[:160]}")

            if stat["skipped"]:
                stat["detail"] += f"｜正文过短丢弃={stat['skipped']}"
            stat["status"] = status_of(meta, stat["errors"])
            stat["elapsed"] = time.perf_counter() - t0
            stats.append(stat)
            print_source_report(stat)
    finally:
        if conn is not None:
            close_conn(conn)

    elapsed = round(time.perf_counter() - started, 2)
    dump_path: Optional[Path] = None
    if args.dump_records:
        dump_path = Path(args.dump_records)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps(
                {
                    "meta": {
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                        "batch_time": batch_time,
                        "elapsed_seconds": elapsed,
                        "sources": [s["key"] for s in sources],
                        "keywords": keywords["global"],
                        "per_source_stats": stats,
                    },
                    "articles": all_records,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print_summary(
        stats,
        elapsed,
        batch_time,
        len(all_records),
        dump_path,
        args.list_only,
        candidate_total=sum(s["collected"] for s in stats),
    )
    return 1 if any(s["status"] == "失败" for s in stats) else 0


if __name__ == "__main__":
    sys.exit(main())
