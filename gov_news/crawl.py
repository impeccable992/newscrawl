"""按 sources.json 的清单批量抓取 -> 按 keywords.json 过滤 -> 输出 JSON（标题 + 正文）。

设计要点
--------
1. **一切靠配置**：源清单在 sources.json（增删条目即可），关键词在 keywords.json。
   代码里不写死任何站点，只做"读配置 → 抓 → 解析 → 过滤 → 落盘"。
2. **每个源可以有自己的抓法**：engine(static/browser)、verify(证书)、列表选择器、正文选择器、
   URL 正则。这些差异是实测出来的（见 sources.json 里每个源的 note），配置化以后不用再改代码。
3. **正文兜底**：没配 body_selector 时，用"文本量占父节点比例最高 + 够长 + 不在导航类容器里"
   的启发式找正文。实测它对政府站不可靠，所以清单里几乎每个源都显式配了是的。
4. **产物固定有两个必填字段**：title 和 body。

用法（仓库根目录执行）
----------------------
    # 全部源，每源 3 篇
    uv run --isolated --extra ai python gov_news/crawl.py --limit 3

    # 只跑指定源（key 用逗号分隔）
    uv run --isolated --extra ai python gov_news/crawl.py --sources ccg,ccg_wqzf,mod --limit 2

    # 临时换关键词（覆盖 keywords.json 的 global）
    uv run --isolated --extra ai python gov_news/crawl.py --keyword 南海,黄岩岛,仁爱礁 --limit 3

    # 只测"列表能不能抓到"（不抓详情页，快）
    uv run --isolated --extra ai python gov_news/crawl.py --list-only

    # 不过滤关键词，全部导出
    uv run --isolated --extra ai python gov_news/crawl.py --no-filter --limit 2
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin, urlparse

from scrapling import Selector
from scrapling.fetchers import Fetcher, StealthyFetcher

HERE = Path(__file__).resolve().parent
_WS = re.compile(r"\s+")
_DATE_PATTERNS = [
    re.compile(r"(20\d{2}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)"),
    re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}(?::\d{2})?)"),
    re.compile(r"(20\d{2}年\d{1,2}月\d{1,2}日)"),
    re.compile(r"(20\d{2}-\d{1,2}-\d{1,2})"),
]
# 正文兜底时要跳过的容器（导航/页脚/推荐位这类会污染正文）
_NOISE_CONTAINER = re.compile(r"(nav|menu|footer|header|side|share|comment|recommend|crumbs|breadcrumb|link|banner)", re.I)


# ---------------------------------------------------------------- 读配置

def load_config(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # 允许在 JSON 里写 _readme 之类的注释字段（json 不支持注释，就用下划线键代替）
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_keywords(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_config(Path(args.keywords)) if Path(args.keywords).exists() else {}
    if args.keyword:
        config["global"] = [k.strip() for k in args.keyword.split(",") if k.strip()]
    config.setdefault("global", [])
    config.setdefault("per_source", {})
    config.setdefault("exclude", [])
    config.setdefault("match", "any")
    return config


# ---------------------------------------------------------------- 抓取

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


def fetch(url: str, source: Dict[str, Any], *, timeout: float = 30.0) -> Tuple[int, str, str]:
    """返回 (状态码, 页面文本, 用的引擎)。

    engine=browser 时走无头隐身浏览器；engine=static 但失败时**自动降级一次浏览器**
    （政府站的 SSL 握手/反爬会偶发抽风，降级比直接报错实用）。
    """
    verify = source.get("verify", True)
    engine = source.get("engine", "static")

    def browser() -> Tuple[int, str, str]:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True, load_dom=True,
                                     block_ads=True, timeout=int(timeout * 1000), verify=verify)
        body = page.body
        text = body.decode(page.encoding or "utf-8", "replace") if isinstance(body, bytes) else (body or "")
        return int(page.status), text, "browser"

    if engine == "browser":
        return browser()

    try:
        page = Fetcher.get(url, impersonate="chrome", stealthy_headers=True, timeout=timeout,
                           retries=1, verify=verify)
    except Exception as exc:
        print(f"    （静态失败 {type(exc).__name__}，降级浏览器重试：{url}）")
        status, text, used = browser()
        return status, text, used + "(降级)"

    body = page.body
    # 政府站常用 gb2312/gb18030，优先用 Scrapling 判定的编码
    text = body.decode(page.encoding or "utf-8", "replace") if isinstance(body, bytes) else (body or "")
    return int(page.status), text, engine


# ---------------------------------------------------------------- 站内检索接口（engine="api"）

_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """清掉检索接口返回的高亮标签（形如 `<span style=color:red>南海</span>`）并反转义实体。"""
    if not text:
        return ""
    return html_unescape(_TAG.sub("", str(text))).strip()


def dig(obj: Any, path: str, default: Any = None) -> Any:
    """按 "data.resultList" 这样的点号路径取值，缺字段不抛异常。"""
    current = obj
    for part in (path or "").split("."):
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
    """接口时间是秒级时间戳（1793262474 -> 2026-10-29 04:27:54），统一成可读格式。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if number > 1e11:  # 毫秒
        number /= 1000
    return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")


def fetch_api(source: Dict[str, Any], params: Dict[str, str]) -> Tuple[int, Any]:
    """调用站内检索接口，返回 (状态码, JSON)。"""
    api = source["api"]
    page = Fetcher.get(api["url"], impersonate="chrome", stealthy_headers=True, params=params,
                       timeout=api.get("timeout", 25), retries=1, verify=api.get("verify", True))
    text = page.body if isinstance(page.body, str) else (page.body or b"").decode("utf-8", "replace")
    return int(page.status), json.loads(text)


def title_key(title: str) -> str:
    """把标题归一化成去重用的键。

    国防部网会把同一篇稿子在多个栏目重复发布（/gfbw/qwfb/、/gfbw/xwfyr/、/gfbw/xwfyr/lxjzhzt/…），
    检索结果里就会出现多条标题相同、URL 不同的记录 —— 按标题去重才是用户想要的"一篇一条"。
    """
    text = re.sub(r"[\s\u3000]+", "", title or "")
    return re.sub(r"[-—–]\s*(中华人民共和国国防部|中国军网|新华网|人民网|央视网)$", "", text)


def list_articles_api(source: Dict[str, Any], limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """把站内检索接口当"列表页"用：每个关键词翻 N 页，合并去重（URL 去重 + 标题去重）。

    配置里的 params 支持两个占位符：{page} 页码、{keyword} 关键词。
    """
    api = source["api"]
    keywords = api.get("search_keywords") or [""]
    pages = int(api.get("pages", 1))
    collected: List[Dict[str, Any]] = []
    seen_urls: set = set()
    seen_titles: set = set()
    status, total = 0, 0
    for keyword in keywords:
        kept = 0
        for page_no in range(1, pages + 1):
            params = {key: str(value).replace("{page}", str(page_no)).replace("{keyword}", keyword)
                      for key, value in (api.get("params") or {}).items()}
            status, data = fetch_api(source, params)
            total = dig(data, api.get("total_path", "data.total"), total) or total
            items = dig(data, api.get("items_path", "data.resultList"), []) or []
            if not items:
                break
            for item in items:
                url = dig(item, api.get("url_field", "manuscriptData.url"), "")
                title = strip_html(dig(item, api.get("title_field", "title"), ""))
                key = title_key(title)
                if not url or url in seen_urls or (key and key in seen_titles):
                    continue
                seen_urls.add(url)
                if key:
                    seen_titles.add(key)
                collected.append({
                    "title": title,
                    "url": url,
                    "published": fmt_epoch(dig(item, api.get("date_field", "issueTime"), "")),
                    "summary": strip_html(dig(item, api.get("desc_field", "desc"), ""))[:400],
                    "category": dig(item, api.get("category_field", "manuscriptData.classify_name"), ""),
                    "search_keyword": keyword,
                })
                kept += 1
                if limit and kept >= limit:
                    break
            if limit and kept >= limit:
                break
    return collected, {"status": status, "total": total, "keywords": keywords}


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
    return {"title": title, "body": body, "published": published,
            "_body_selector_used": body_selector_used}


def list_articles(html: str, source: Dict[str, Any]) -> List[Dict[str, str]]:
    """从列表页抽出 (标题, 绝对 URL) 候选，按 link_re 过滤、按 URL 去重。"""
    page = Selector(content=html)
    link_re = re.compile(source["link_re"]) if source.get("link_re") else None
    seen, articles = set(), []
    for anchor in page.css(source.get("list_selector", "a")):
        href = (anchor.attrib.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = urljoin(source["url"], href)
        absolute = absolute.split("#")[0]
        if link_re and not link_re.search(absolute):
            continue
        # 同源校验：允许子域（www.people.com.cn 的列表页里，文章常在 politics.people.com.cn）
        if not _same_site(absolute, source["url"]):
            continue
        label = _WS.sub(" ", " ".join(anchor.css("::text").getall())).strip()
        if len(label) < 6 or absolute in seen:
            continue
        seen.add(absolute)
        articles.append({"title": label, "url": absolute})
    return articles


# ---------------------------------------------------------------- 关键词

def match_keywords(article: Dict[str, Any], keywords: Dict[str, Any], source_key: str) -> List[str]:
    """返回命中的关键词；空列表 = 不保留（除非用 --no-filter 关掉过滤）。"""
    haystack = f"{article.get('title', '')} {article.get('body', '')}".lower()

    for word in keywords.get("exclude", []):
        if word and word.lower() in haystack:
            return []

    wanted = list(keywords.get("global", [])) + list(keywords.get("per_source", {}).get(source_key, []))
    if not wanted:
        return []
    hits = [w for w in wanted if w.lower() in haystack]
    if keywords.get("match") == "all":
        return hits if len(hits) == len(set(wanted)) else []
    return hits


# ---------------------------------------------------------------- 主流程

def main() -> None:
    parser = argparse.ArgumentParser(description="按配置批量抓官网新闻 -> JSON（标题+正文+关键词过滤）")
    parser.add_argument("--config", default=str(HERE / "sources.json"))
    parser.add_argument("--keywords", default=str(HERE / "keywords.json"))
    parser.add_argument("--sources", default="", help="只跑指定源（key 逗号分隔），默认全部启用的源")
    parser.add_argument("--limit", type=int, default=0, help="每源最多几篇（0=用配置里的 limit）")
    parser.add_argument("--keyword", default="", help="临时覆盖全局关键词（逗号分隔）")
    parser.add_argument("--no-filter", action="store_true", help="不做关键词过滤，全部导出")
    parser.add_argument("--list-only", action="store_true", help="只抓列表页，不抓详情页")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default="", help="产物路径（默认 out/gov_news_<时间戳>.json）")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    keywords = load_keywords(args)
    wanted_keys = {k.strip() for k in args.sources.split(",") if k.strip()}
    sources = [s for s in config["sources"]
               if s.get("enabled", True) and (not wanted_keys or s["key"] in wanted_keys)]

    print(f"配置：{args.config}")
    print(f"关键词：global={keywords['global']}")
    print(f"        per_source={ {k: v for k, v in keywords['per_source'].items() if k in [s['key'] for s in sources]} }")
    print(f"        exclude={keywords['exclude']}  match={keywords['match']}")
    print(f"待抓 {len(sources)} 个源：{', '.join(s['name'] for s in sources)}\n")

    results: List[Dict[str, Any]] = []
    stats: List[Dict[str, Any]] = []
    started = time.perf_counter()

    for source in sources:
        limit = args.limit or source.get("limit", 5)
        stat = {"key": source["key"], "name": source["name"], "url": source["url"],
                "engine": source.get("engine", "static"), "list_status": 0,
                "found": 0, "fetched": 0, "kept": 0, "errors": []}
        t0 = time.perf_counter()
        try:
            if source.get("engine") == "api":
                # 站内检索接口当列表源：关键词由站点自己筛过，结果按页取回
                candidates, api_meta = list_articles_api(source, limit)
                stat["list_status"] = api_meta["status"]
                stat["engine"] = "api"
                stat["site_total"] = api_meta["total"]
                stat["found"] = len(candidates)
                print(f"[{source['name']}] 检索接口 {api_meta['status']} 站内共 {api_meta['total']} 条 "
                      f"关键词={api_meta['keywords']} 取回 {len(candidates)} 篇 "
                      f"({time.perf_counter() - t0:.1f}s)")
            else:
                status, html, engine = fetch(source["url"], source, timeout=args.timeout)
                stat["list_status"] = status
                stat["engine"] = engine
                candidates = list_articles(html, source)
                stat["found"] = len(candidates)
                print(f"[{source['name']}] 列表页 {status} 引擎={engine} 候选 {len(candidates)} 篇 "
                      f"({time.perf_counter() - t0:.1f}s)")
        except Exception as exc:
            stat["errors"].append(f"列表页失败：{type(exc).__name__}: {str(exc)[:120]}")
            stats.append(stat)
            print(f"[{source['name']}] ❌ 列表页失败 {type(exc).__name__}: {str(exc)[:80]}")
            continue

        picked = candidates[:limit]
        if args.list_only:
            for idx, item in enumerate(picked, 1):
                print(f"    {idx}. {item['title'][:40]} -> {item['url'][:80]}")
            stat["kept"] = len(picked)
            stats.append(stat)
            continue

        # 详情页单独一套引擎：检索源(api)的详情页仍是普通网页，用 detail_engine 抓
        detail_source = {**source,
                         "engine": source.get("detail_engine", "static"),
                         "verify": source.get("detail_verify", source.get("verify", True))}

        for idx, item in enumerate(picked, 1):
            try:
                detail_status, detail_html, _ = fetch(item["url"], detail_source, timeout=args.timeout)
                parsed = parse_article(detail_html, source)
                record = {
                    "source": source["key"],
                    "source_name": source["name"],
                    "url": item["url"],
                    "list_title": item["title"],
                    "title": parsed["title"] or item["title"],
                    "body": parsed["body"],
                    "body_length": len(parsed["body"]),
                    # 检索接口给的发布时间比页面正则更准，优先用
                    "published": item.get("published") or parsed["published"],
                    "summary": item.get("summary", ""),
                    "category": item.get("category", ""),
                    "search_keyword": item.get("search_keyword", ""),
                    "http_status": detail_status,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "_body_source": parsed["_body_selector_used"],
                }
                stat["fetched"] += 1
                # 太短的正文多半是"记者会索引页/图片快讯"，可用 min_body_length 丢掉
                if record["body_length"] < source.get("min_body_length", 0):
                    stat.setdefault("skipped_short", 0)
                    stat["skipped_short"] += 1
                    print(f"    ~ [{source['name']}] {record['title'][:34]} 正文仅{record['body_length']}字，按 min_body_length 丢弃")
                    continue
                # skip_keyword_filter：检索源已经按关键词筛过（服务端），不再本地筛一遍
                hits = [] if source.get("skip_keyword_filter") else match_keywords(record, keywords, source["key"])
                if source.get("skip_keyword_filter") and record.get("search_keyword"):
                    hits = [record["search_keyword"]]     # 回填"为什么留下它"，便于溯源
                if args.no_filter or hits or source.get("skip_keyword_filter"):
                    record["matched_keywords"] = hits
                    results.append(record)
                    stat["kept"] += 1
                    print(f"    ✓ [{source['name']}] {record['title'][:36]} "
                          f"正文{record['body_length']}字 命中{hits}")
                else:
                    print(f"    - [{source['name']}] {record['title'][:36]} 未命中关键词")
            except Exception as exc:
                stat["errors"].append(f"{item['url']}: {type(exc).__name__}: {str(exc)[:100]}")
                print(f"    ✗ {item['url'][:70]} -> {type(exc).__name__}: {str(exc)[:70]}")
        stats.append(stat)

    elapsed = round(time.perf_counter() - started, 2)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": elapsed,
            "sources_configured": [s["key"] for s in sources],
            "keywords": {"global": keywords["global"], "match": keywords["match"],
                         "exclude": keywords["exclude"], "per_source": keywords["per_source"]},
            "filter_enabled": not args.no_filter,
            "articles": len(results),
            "per_source_stats": stats,
        },
        "articles": results,
    }
    out_path = Path(args.out) if args.out else HERE / "out" / f"gov_news_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n===== 汇总（{elapsed}s）=====")
    for stat in stats:
        print(f"  {stat['name']:<22} 候选{stat['found']:>3} 抓取{stat['fetched']:>3} "
              f"命中{stat['kept']:>3}  错误{len(stat['errors'])}")
    print(f"\n共导出 {len(results)} 篇 -> {out_path}")


if __name__ == "__main__":
    main()
