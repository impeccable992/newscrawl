# 官网/涉海机构新闻采集（站内检索优先 + 入库）

按 `sources.json` 的源清单采集官网/涉海机构新闻：**先走站点自己的检索（站内检索优先），检索不可用/
失败/0 条时自动回退该源的列表页**，进详情页取标题+正文，按 `keywords.json` 过滤，再写进 MySQL（每源一表）。
控制台给每源的**检索方式 + 状态 + 四个计数**，末尾一张汇总表；有任一源失败时进程退出码非 0。

```bash
# 全部启用的源，每源 2 篇，写库（需要 gov_news/db.py 就绪）
uv run --isolated --extra ai --with pymysql python gov_news/crawl.py --limit 2

# 只跑纯静态检索的源，不写库（没有 db.py 也能完整验证检索/报告）
uv run --isolated --extra ai python gov_news/crawl.py --sources mod,mod_qwfb,mil81,people --limit 2 --no-db

# 无站内检索的源：报告会标「无站内检索→回退列表页」
uv run --isolated --extra ai python gov_news/crawl.py --sources ccg,nanhai --limit 2 --no-db

# scspi 只有加代理才通（不加会失败并回退，退出码非 0）
uv run --isolated --extra ai python gov_news/crawl.py --sources scspi --limit 2 --no-db --proxy http://127.0.0.1:7897

# 临时换检索词（覆盖 keywords.json 的 global）
uv run --isolated --extra ai python gov_news/crawl.py --keyword 南海,黄岩岛 --limit 3 --no-db

# 只取候选、不抓详情不入库（最快，用来看"检索到底能取回什么"）
uv run --isolated --extra ai python gov_news/crawl.py --sources mod --limit 10 --list-only

# 显式导出本轮结果（默认不导出；导出的文件可直接被 --from-json 回灌）
uv run --isolated --extra ai python gov_news/crawl.py --sources people --limit 3 --no-db --dump-records gov_news/out/people_dump.json

# 把已有 JSON 回灌入库（这条路径只入库，不抓取）
uv run --isolated --extra ai --with pymysql python gov_news/crawl.py --from-json gov_news/out/gov_news_20261117_161824.json
```

## 命令行参数（与 `--help` 逐条对应）

| 参数 | 说明 |
|---|---|
| `--config` | 源清单路径，默认 `gov_news/sources.json` |
| `--keywords` | 关键词表路径，默认 `gov_news/keywords.json` |
| `--sources` | 只跑指定源（key 逗号分隔），默认全部启用的源 |
| `--limit` | 每源最多几篇（0=用配置里的 `limit`）；检索路径取够这个数就提前停 |
| `--keyword` | 临时覆盖本次的**检索词**，同时替换 `keywords.json` 的 `global` **过滤词**（逗号分隔）；`per_source` 不受影响，回退路径仍会追加 |
| `--no-filter` | 回退列表页时不做关键词过滤，全部保留（检索路径本来就不本地筛）；日志里这类条目标「保留（--no-filter 未做过滤）」而不是「丢弃」 |
| `--list-only` | 只取候选列表，不抓详情页、不入库 |
| `--search-pages` | 每个检索词翻几页（0=用源配置，上限 20） |
| `--delay` | 请求间隔秒数（必须 ≥1，默认 1.0） |
| `--proxy` | 代理地址，**只对配置里 `search.proxy=true` 的源生效**（目前只有 scspi） |
| `--no-db` | 不写库（只做检索/报告，便于离线验证） |
| `--dump-records` | 把本轮结果导出到指定 JSON 路径（默认不导出） |
| `--from-json` | 不抓取，直接把指定 JSON 回灌入库 |
| `--db-name` | MySQL 库名，默认 `newscrawl` |
| `--timeout` | 单个请求超时秒数，默认 30 |
| `--verbose` | 打印每个请求的 INFO 日志（默认只留报告） |

## 采集模式：检索优先 → 列表页兜底

每个源先看 `sources.json` 里自己的 `search.type`：

| `search.type` | 做法 |
|---|---|
| `api` | 直接请求站点的检索 JSON 接口（GET 或 POST + JSON body + 自定义头） |
| `results_page` | 请求"结果页"HTML，按 `list_selector` + `link_re` 抽链接（scspi） |
| `browser` | 用隐身浏览器打开页面/在页面里 fetch 取数接口（xinhua 系；静态直调 403） |
| `none` | 确认没有可用站内检索，直接走列表页 |

- 检索**取到候选**就用检索结果，报告标 `api(...)` / `results_page` / `browser`，记录的 `match_level` 记 `server`
  （服务端已按检索词筛过，不再本地筛一遍）。
- 检索**不可用/报错/0 条**时自动回退该源 `url` 的列表页，报告标 `…→回退列表页`，状态记「回退列表页」，
  `·` 行保留回退原因；回退路径按 `global + per_source` 在标题+正文里本地过滤，`match_level` 记
  `title` / `content` / `title+content`。
- **两条路都失败**才算该源失败（状态「失败」，进程退出码 1）。

**词表口径（两张表各管一段）**：

| 表 | 作用 | 什么时候用 |
|---|---|---|
| `keywords.global`（`--keyword` 覆盖它） | 既是**检索词**（每个源把每个词各搜一遍），也是**回退过滤词** | 两种模式都用，口径一致 |
| `keywords.per_source[key]` | **只在回退列表页时追加**到过滤词，不改检索词 | 仅回退路径 |

所以：检索源的 `matched_keywords` 记服务端实际用的那个 `search_keyword`（来自 `global`），
回退源的 `matched_keywords` 记本地命中的词（可能来自 `global`，也可能来自 `per_source`）——
日志里命中词后面会标 `(global)` / `(per_source)`，一眼能看出这条是「按检索词留的」还是
「按本源补充词留的」。

## 每源报告与四个计数

```
[人民网/people] 检索方式=api(POST) 状态=成功
    检索命中=3  详情抓取=3  入库=3  错误=0  耗时 6.4s
    检索=api 页数=5｜站内总数=南海:10052｜HTTP=200
    ✓ [人民网] 生态环境部就南海问题答记者问 保留 检索词=南海(global，服务端筛)
    ✓ [中国海警局] 中国海警位黄岩岛领海执法巡查 保留 命中=[管控(per_source)]
    - [中国海警局] 某条无关新闻 丢弃原因=未命中过滤词 查看词=[南海, 仁爱礁, 维权执法…另4个] 命中=[]
    ✓ [央视网] 某条新闻 保留（--no-filter 未做过滤，查看词=[南海, 仁爱礁, 军事, 国防…另2个]）
```

- **检索命中数**：检索/回退取回的候选条数（回退时记在 `列表候选=` 里，检索命中记 0）。
- **详情抓取数**：真正抓到详情页的篇数（含后来被 `min_body_length` 丢掉、被关键词筛掉的）。
- **入库数**：`upsert_records` 返回的 `inserted + updated`；未写库时显示 `0(未入库)` / `0(未就绪)`。
- **错误数**：真实失败数（网络/解析/入库）。配置里标注"已知不可用"的检索不算错误，只在 `·` 行注明原因。
- 回退源的详情行里额外给 `过滤词=[…]`（按顺序去重后的 `global + per_source`），
  对应那条 `- … 查看词=[…]` 用的就是这一份；超长会截断成 `…另N个`。

### 每条记录的保留/丢弃日志

每条详情页都会打一行，措辞按 `keep` 而不是按"命中数"决定，避免「留下了却打印未命中」：

```
    ✓ [人民网] 生态环境部就南海问题答记者问 保留 检索词=南海(global，服务端筛)
    ✓ [中国海警局] 中国海警位黄岩岛领海执法巡查 保留 命中=[管控(per_source)]
    - [中国海警局] 某条无关新闻 丢弃原因=未命中过滤词 查看词=[南海, 仁爱礁, 维权执法…另4个] 命中=[]
    - [中国海警局] 某条招聘公告 丢弃原因=命中排除词(招聘) 查看词=[南海, 仁爱礁, 维权执法…另4个]
    ✓ [央视网] 某条新闻 保留（--no-filter 未做过滤，查看词=[南海, 仁爱礁, 军事, 国防…另2个]）
```

- `✓` = 保留（并给出依据：检索源给服务端用的检索词，回退源给本地命中词及来源）；
- `-` = 丢弃（给出丢弃原因 + 查了哪些词 + 命中了哪些词）；
- 命中词标 `(global)` / `(per_source)`，能看出是「按检索词留的」还是「按本源补充词留的」；
- `--no-filter` 下不会出现「未命中」「丢弃」字样，只写「保留（--no-filter 未做过滤）」。

### 状态口径

| 状态 | 含义 |
|---|---|
| `成功` | 走检索路径且取到候选（回退、错误都没有） |
| `部分成功` | 取到候选了，但详情抓取/入库出现过错误 |
| `检索无结果` | 走检索路径、`检索命中=0` 且**没有回退**。注意：现有 `collect_candidates()` 在检索 0 条时**一定会去回退**，所以这一档只在"检索 0 条 + 列表页成功"以外的路径上出现（`--list-only` 实测仍会回退）；真实源目前都会落到「回退列表页」 |
| `回退列表页` | 走了列表页兜底；`·` 行里保留回退原因（检索不可用/报错/0 条） |
| `失败` | 检索与列表页两条路都没取到候选（进程退出码 1） |

末尾汇总表列出全部源的同名列，再给「共保留 N 篇；失败源 …」；若有「检索无结果」的源会另起一行列出。
`失败源 N 个` 只统计「失败」，新状态不会被算进去。

## 入库（`gov_news/db.py`）

`crawl.py` 延迟导入同一目录下的 `db.py`，按下面契约调用（字段名以 db.py 为准）：

```python
from db import connect, ensure_schema, upsert_records, load_records_from_json
conn = connect(db_name="newscrawl")
ensure_schema(conn, source_key, source_name)
upsert_records(conn, source_key, source_name, records, batch_time)   # -> {"inserted","updated","skipped","errors"}
load_records_from_json(conn, path, batch_time=...)                   # --from-json 用
```

- **同一轮共用一个 `batch_time`**（启动时间，`--dump-records` 的 `meta.batch_time` 里也能看到）。
- 一条记录的关键字段：`title` / `list_title` / `body` / `body_length` / `published` / `summary` / `category` /
  `search_keyword` / `matched_keywords` / `match_level` / `image_urls`（正文前 3 张图片的**外链地址**，不下载）/
  `http_status` / `fetched_at` / `_body_source`。
- `db.py` 不存在时：**入库路径明确报「db.py 尚未就绪」并跳过**，检索/报告/导出照常跑（退出码只看源失败）；
  `--from-json` 因为只做入库，会直接报错并以退出码 2 结束。

## 产物与回灌

- 默认**不写 `out/*.json`**，结果只入库。
- `--dump-records PATH` 才会导出 JSON（结构 `{"meta": {...}, "articles": [...]}`，`meta.per_source_stats`
  里就是报告里的四个计数），导出的文件可以直接喂给 `--from-json`。

## `sources.json` 的 `search` 配置块

| 字段 | 说明 |
|---|---|
| `type` | `api` / `results_page` / `browser` / `none` |
| `method` | `api` 型用 `GET` 或 `POST`（people 是 POST） |
| `url` / `url_template` | 接口地址 / 结果页与检索页模板，支持 `{keyword}`、`{page}`（`{keyword}` 自动 percent-encode） |
| `params` / `body` | GET 查询参数 / POST 的 JSON body，值里同样支持 `{keyword}`、`{page}` |
| `headers` | 自定义请求头（people 需要 `Origin` / `Referer`） |
| `pages` / `page_start` | 每个词翻几页（默认 5、上限 20）/ 页码起点 |
| `items_path` / `total_path` | JSON 里结果数组与总数的路径 |
| `title_field` / `url_field` / `date_field` / `summary_field` / `category_field` | 字段映射 |
| `link_re` / `url_prefixes` | 检索后的**二次过滤**：频道型源靠它收窄（`mod_qwfb`→`/gfbw/`、`people_mil`→`military.` 域名、xinhua 两频道→URL 前缀） |
| `proxy` | 该源是否需要代理（只有 scspi 为 `true`；地址由 `--proxy` 提供） |
| `js_fetch.url_template` | `browser` 型专用：先打开 `url_template` 页面，再**在页面上下文里 `fetch`** 这个地址（xinhua 系） |
| `xhr_url_re` | `browser` 型备选：从会话录到的 XHR 里按正则找最近一条 JSON 响应 |
| `page_action` | `browser` 型备选：`{fill_selector, press, wait_ms, after_ms}`，填检索框后回车 |
| `unavailable` / `unavailable_reason` | 调研已确认取不到结果：配置留档，运行时直接回退列表页并在报告里注明原因 |
| `verify` | 检索请求是否校验证书（mod / mil81 必须 `false`） |
| `note` | 本源的检索坑 |

列表页兜底与详情页仍用源上的老字段：`url` / `engine` / `verify` / `list_selector` / `link_re` /
`title_selector` / `body_selector` / `limit` / `min_body_length`。
`detail_engine` / `detail_verify` 单独指定详情页引擎（不写则用 `engine` / `verify`）。

## `keywords.json`

- `global`：**既是本地过滤词，也是站内检索的统一词表**（一个源把每个词各搜一遍）；
  `--keyword` 覆盖它时两者一起换。检索模式与回退模式共用这一份，口径一致。
- `per_source`：只在**回退列表页**时**追加**到过滤词（不改检索词，检索路径由服务端筛）。
  命中来源会在日志里标 `(global)` / `(per_source)`。
- `exclude` / `match`：同以前（命中排除词即丢弃；`all` 表示必须全部命中）。

## 各源检索方式与实测读数（2026-11-20）

| 源 | 检索方式 | 实测读数 | 说明 |
|---|---|---|---|
| `mod` 国防部网 | `api` GET | 200，站内 南海=430 / 黄岩岛=81 | `mod-search.mod.gov.cn`、`channelId=718`、`verify=false` |
| `mod_qwfb` 记者会专题 | `api` GET | 同 mod（复用 718） | 检索后按 `/gfbw/` 前缀过滤 |
| `mil81` 中国军网 | `api` GET | 200，站内 南海=566 | 自有后端 `mil-search.81.cn`、`channelId=636`；**返回的是 `www.81.cn`**，`link_re` 同时容纳两者 |
| `people` 人民网 | `api` POST | 200，站内 南海=10052 | body 里 `hasTitle=true`；带 `Origin`/`Referer`；标题摘要带 `<em>` → `strip_html` |
| `people_mil` 人民网·军事 | `api` POST | 同上（复用 people） | 按 `military.people.com.cn` 域名过滤 |
| `scspi` 南海战略态势感知 | `results_page` GET | **不加代理：SSLError/超时失败；加代理：200、命中 3 条** | 唯一需要 `--proxy` 的源 |
| `xinhua` 新华网 | `browser` + 页面内 `fetch` | 200，站内 南海=5686；2 页取回 12 条 | 取数接口静态直调恒 403，只能页面里发；偶发 503 限流 |
| `xinhua_mil` 新华网·军事 | `browser` + 前缀过滤 | 5 页×2 词约 100 条里 `/milpro/` **命中 0** | 多走回退列表页（列表候选 106） |
| `xinhua_world` 新华网·国际 | `browser` + 前缀过滤 | 2 页×2 词未命中 `/world/`；单源耗时 **38.0s → 24.0s** | 见「xinhua 系耗时」 |
| `cctv` 央视网 | `browser`（留档不可用） | 结果列表取不到 | 回退列表页（候选 84） |
| `fmprc` 外交部 | `browser`（留档不可用） | IRS 检索页 0 条、接口缺凭证 | 回退列表页（候选 63） |
| `ccg` 中国海警局 | `none` | — | 跳转页结果恒 0、后端 404；回退列表页（候选 11） |
| `ccg_wqzf` 海警·维权执法 | `none` | — | 回退列表页（候选 19±） |
| `nanhai` 中国南海研究院 | `none` | — | 表单是空壳；回退列表页（候选 8） |
| `mod_search_scs` | — | 已 `enabled=false`、`deprecated` | 最早的"检索接口当列表页"实验，已被 `mod` 的通用检索取代，条目与参数保留供追溯 |

## xinhua 系耗时（2026-11-20 实测）

全量联调里 `xinhua_world` 单源 38.0s（同批其他源 3–19s），逐段量过（脚本在 `gov_news/out/measure_xinhua_*.py`）：

| 量法 | 读数 | 结论 |
|---|---|---|
| 每个 (词, 页) 重新 `goto https://so.news.cn/` 一次（`network_idle=True`） | 4 页合计 22.02s（单页 5.3–7.0s） | 单一导航 **5–7s**，**是主要构成** |
| 同上但 `network_idle=False` | 4 页合计 25.95s | 关掉 `network_idle` **不提速**（反而更慢），所以不动它 |
| 一次导航 + 页内连续 `fetch` curPage=1/2/3 | 导航 1.83s + 三次 fetch 1.42/1.08/1.71s = **6.04s**，每页仍是 10 条、页码顺序正确 | 检索页固定时没有理由反复导航 |
| 详情页 `fetch()`（静态 200，真实 URL） | 0.19s / 0.22s，一次慢到 4.17s | 详情**不是**主要构成 |

据此改成：**检索页不变（只有 `curPage` 变）时复用同一页面做页内 fetch**，页面被关掉/崩掉才重新导航
（`reuse_page()` 会先探活，失败则重新 `goto` 一次再试）；**不改 `network_idle`、不缩超时、不减词/页**。

`xinhua_world` 单源耗时（`--sources xinhua_world --limit 3`，同一台机）：**38.0s → 24.0s（检索通、命中 1 条）**；
撞上 `so.news.cn` 偶发 503 时是 **22.0s（检索 503 → 回退列表页、抓 3 条详情）**——503 是站点侧风控，
与本改动无关，两种情况下都不再是 30s+。

## 复跑用的核对脚本（都在 `gov_news/out/`，该目录不入库）

`out/` 下的 `measure_*.py` / `dev_selftest_*.py` 是排查与验收用的只读脚本，改完 `crawl.py` 想复核时直接跑：

```bash
uv run --isolated --extra ai python gov_news/out/dev_selftest_status.py       # 状态映射 + 关键词过滤 + 日志措辞（离线）
uv run --isolated --extra ai python gov_news/out/dev_selftest_status_cli.py   # 伪造"检索 0 条"的源，看报告/汇总怎么标
uv run --isolated --extra ai python gov_news/out/measure_xinhua_timing.py --pages 2 --keywords 南海,仁爱礁   # network_idle 开关对比
uv run --isolated --extra ai python gov_news/out/measure_xinhua_reuse.py      # 一次导航 vs 每次导航
uv run --isolated --extra ai python gov_news/out/measure_xinhua_phases.py     # 检索/详情分段耗时
uv run --isolated --extra ai python gov_news/out/measure_detail_cost.py       # 详情页逐条耗时（用真实检索结果里的 URL）
```

## 代理与证书纪律

- `http://127.0.0.1:7897` **只给需要翻墙的站点**；本清单一律不写死进代码，只由 `--proxy` 传入。
- 14 个国内源**全部直连**；只有 `scspi` 配了 `search.proxy=true`（实测不加代理 3/3 失败）。
- `mod` / `mod_qwfb` / `mil81` 的证书与域名不匹配（`CN=*.unionpayintl.com`），必须 `verify=false`；
  详情页走 `detail_verify` 覆盖。

## 坑与已知限制

1. **`people` 的 `<em>` 高亮**：检索接口返回的 `title` 和 `content` 里带 `<em>南海</em>`，标题/摘要入库前
   都走 `strip_html()`（实测导出里 `em=False`），关键词匹配与 `match_level` 也用的是清洗后的文本。
2. **`cctv` 的 `link_re` 必须大小写都收**：实际链接形如
   `https://tv.cctv.com/2026/11/19/VIDEjRRYczjjaRgmVYUa7Zfj261119.shtml`，只写 `VIDE` 或只写 `VID[A-Z0-9]+`
   都会漏，现为 `VID[A-Za-z0-9]+`（改前实测候选 0，改后 84）。
3. **`xinhua` 检索页是 hash 路由**：直接改 hash 属于"同文档导航"，Playwright 拿不到新响应（报
   `Failed to get response`），因此实现改成"每页重开基础页 + 在页面里 fetch 取数接口"。
4. **`so.news.cn/getNews` 会偶发 503**：连续多页/多源调用时出现（调大 `--delay` 只能缓解），
   该页按错误记入并继续，整源仍可能回退列表页。
5. **`mil81` 详情页 `h1` 常缺**：标题会退到 `<title>`，带上 `- 中国军网` 后缀（去重用的 `title_key` 会剥掉）。
6. **图片只存前 3 张外链**：在正文容器里按出现顺序取绝对 `http(s)` 地址，站标/导航图标因为不在正文容器里
   已被排除，但不下载、不校验可用性。
7. **只抓列表首页**：海警维权执法有 24 页分页（`index_1.html`…），回退路径仍只抓第 1 页。
8. **`--delay` 下限 1s**：低于 1 会被参数校验拒绝（对国内站点的礼貌间隔）。

## 加一个源（改 JSON 就行）

```json
{
  "key": "example", "name": "某部门官网", "url": "https://www.example.gov.cn/",
  "enabled": true, "engine": "static", "verify": true,
  "list_selector": "ul.news-list li a",
  "link_re": "example\\.gov\\.cn/.+\\d{6}\\.html$",
  "title_selector": "h1", "body_selector": ".article-content", "limit": 5,
  "search": {
    "type": "api", "method": "GET",
    "url": "https://so.example.gov.cn/api/search",
    "params": {"pageNumber": "{page}", "pageSize": 10, "title": "{keyword}"},
    "pages": 5, "page_start": 1,
    "items_path": "data.list", "total_path": "data.total",
    "title_field": "title", "url_field": "url", "date_field": "publishTime",
    "link_re": "example\\.gov\\.cn/.+\\d{6}\\.html$",
    "note": "怎么找到这个接口的，写在这里"
  },
  "note": "为什么这么配，写在这里"
}
```

找不到可用接口时：`search.type` 写 `none`（走列表页），或用 `browser` + `js_fetch` / `xhr_url_re` /
`dom_link_selector` 其中之一；确认站点没有检索能力就把原因写进 `search.note`。
