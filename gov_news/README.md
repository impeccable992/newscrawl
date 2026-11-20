# 官网/涉海机构新闻批量抓取（配置化）

按 `sources.json` 里的源清单批量抓列表页 → 进详情页取**标题 + 正文** → 按 `keywords.json` 过滤 → 输出 JSON。
增删源、改关键词都只改这两个 JSON，不用动代码。

```bash
# 全部源，每源 3 篇
uv run --isolated --extra ai python gov_news/crawl.py --limit 3

# 只跑指定源（key 逗号分隔）
uv run --isolated --extra ai python gov_news/crawl.py --sources ccg,ccg_wqzf,mod --limit 2

# 临时换关键词（覆盖 keywords.json 的 global）
uv run --isolated --extra ai python gov_news/crawl.py --keyword 南海,黄岩岛,仁爱礁,钓鱼岛 --limit 3

# 只验证"列表能不能抓到"（不抓详情，很快）
uv run --isolated --extra ai python gov_news/crawl.py --list-only

# 不过滤，全部导出
uv run --isolated --extra ai python gov_news/crawl.py --no-filter --limit 1
```

产物：`gov_news/out/gov_news_<时间戳>.json`

## 加一个源（改 JSON 就行）

在 `sources.json` 的 `sources` 数组里加一条：

```json
{
  "key": "example",
  "name": "某部门官网",
  "url": "https://www.example.gov.cn/",
  "enabled": true,
  "engine": "static",
  "verify": true,
  "list_selector": "ul.news-list li a",
  "link_re": "example\\.gov\\.cn/.+\\d{6,}\\.html$",
  "title_selector": "h1",
  "body_selector": ".article-content",
  "limit": 5,
  "note": "为什么这么配，写在这里"
}
```

字段含义（`sources.json` 里的 `_readme` 也有）：

| 字段 | 作用 |
|---|---|
| `enabled` | 暂时不抓就改 false，不必删条目 |
| `engine` | `static`=HTTP 直取（快）；`browser`=隐身浏览器（慢，用于 JS/反爬站点） |
| `verify` | `false` 用于证书与域名不匹配的站（如 mod.gov.cn、81.mil.cn） |
| `list_selector` | 列表页里定位文章链接的 CSS（可逗号分隔多个） |
| `link_re` | 对**绝对 URL** 的正则过滤，只留文章页（挡住栏目页/首页/外链） |
| `title_selector` / `body_selector` | 详情页标题/正文容器，留空则自动兜底 |
| `limit` | 每源默认抓几篇（`--limit` 可覆盖） |

静态抓取失败会**自动降级重试一次浏览器**（政府站 SSL 握手偶发抽风时不至于整源失败）。

## 站内检索源（engine="api"）—— 查特定主题最有效

聚合站的首页排的是"时政要闻"，想出"南海"这种主题内容，用**站点自己的检索**当列表源最省事。
国防部网就是一个现成例子（`mod_search_scs`）：它的检索页 HTML 是**空骨架**（结果由 `index.js`
异步填进去，静态抓只会拿到"正在加载查询结果"），但页面调的检索接口是可直接请求的：

```
GET http://mod-search.mod.gov.cn/api-surface/es/docSearchEasy
    ?pageNumber=1&pageSize=10&title=南海&searchType=2&indexNames=manuscript&channelId=718
→ {"code":200,"data":{"total":430,"resultList":[{"title":"…<span style=color:red>南海</span>…",
   "issueTime":1793262474,"manuscriptData":{"url":"…","classify_name":"2026年10月"}}, …]}}
```

（接口是从 `/template/tenant207/t681/js/index.js` 里扒出来的：`indexsearch` 1=标准、2=模糊、3=二次检索；
`searchfield` TITLE→`title` 参数、CONTENT→`content`、AUTHOR→`author`；`channelId=718` 是国防部网主站。）

配置写法：

```json
{
  "key": "mod_search_scs", "name": "国防部网·站内检索(南海)",
  "engine": "api", "skip_keyword_filter": true,
  "detail_engine": "static", "detail_verify": false,
  "api": {
    "url": "http://mod-search.mod.gov.cn/api-surface/es/docSearchEasy",
    "params": {"pageNumber": "{page}", "pageSize": 10, "title": "{keyword}",
               "searchType": 2, "indexNames": "manuscript", "highlightType": 2,
               "returnFields": "", "channelId": 718},
    "search_keywords": ["南海"],        // 多个词会逐个查再合并
    "pages": 6,                          // 每个词翻几页（每页 10 条）
    "items_path": "data.resultList", "total_path": "data.total",
    "title_field": "title", "url_field": "manuscriptData.url",
    "date_field": "issueTime", "desc_field": "desc",
    "category_field": "manuscriptData.classify_name"
  },
  "title_selector": "h1", "body_selector": ".article-content", "limit": 12
}
```

要点：

- `params` 里支持 `{page}`、`{keyword}` 两个占位符，其余原样传。
- `skip_keyword_filter: true`：**站内检索已在服务端筛过关键词**，不用再本地筛一遍
  （否则你本地只写了"南海"，搜"南沙"出来的条目反而会被丢掉）。
- **按标题去重**：国防部网会把同一篇稿子在 `/gfbw/qwfb/`、`/gfbw/xwfyr/`、`/gfbw/xwfyr/lxjzhzt/`
  等多个栏目重复发布，检索结果里会出现多条"标题相同、URL 不同"。代码按归一化标题去重，
  12 条原始结果 → 1 条稿件。
- `min_body_length`（可选）：正文短于这个字数就丢弃。国防部网的"记者会索引页"只有 36~38 字
  （真正内容在它的子链接里），设 60 就能把这些空壳过滤掉，实测 20 条候选留下 17 篇。
- 站内共 430 条南海结果：把 `pages` 提到 43（`limit` 提到 430）就能全量拉；
  `--list-only` 只列标题和 URL，不抓正文，很快。

实测（`pages: 10`, `limit: 20`, `min_body_length: 60`）：

```
[国防部网·站内检索(南海)] 检索接口 200 站内共 430 条 关键词=['南海'] 取回 20 篇 (2.4s)
    ✓ 【双语】国防部：中方将以有力行动遏制任何祸乱南海的行径 正文2130字 命中['南海']
    ✓ 正告菲律宾：南海不是以“人道”之名肆意表演的戏台 正文1180字 命中['南海']
    ✓ 海军福建舰赴南海相关海域开展科研试验和训练任务 正文118字 命中['南海']
    …
共导出 17 篇（5.3s，0 错误）
```

其他站点同理：先在它的检索页上打开浏览器开发者工具（或看页面的 `*.js`）找结果接口，
再照上面加一条 `engine: "api"` 的源。找不到接口时，退回 `engine: "browser"` 抓检索页
（JS 渲染后的 DOM 里有结果）。

## 改关键词

`keywords.json`：

- `global`：任何源命中即保留（在**标题+正文**里匹配）
- `per_source`：只对该 `key` 生效的补充词（该源命中 global 或 per_source 任一即可）
- `exclude`：命中即丢弃（用来压掉招聘、海洋环境预警报这类噪声）
- `match`：`any`（默认，任一词命中）/ `all`（必须全部命中）

本次实测的过滤效果举例：海警局那篇《中国海警位中国台湾岛以东海域依法开展常态化执法巡查》
命中 `海警 / 执法巡查 / 管控`；而《未来一周海洋环境预警报》会被 `exclude` 里的
`海洋环境预警报` 丢掉。

## 产物字段

| 字段 | 说明 |
|---|---|
| `title` | 详情页标题（`title_selector` → `h1` → `<title>` 依次兜底） |
| `body` | **正文全文**（纯文本，已压空白），`body_length` 是其字符数 |
| `published` | 从标题/正文/页面里正则抓到的发布时间（拿不到就空） |
| `url` / `source` / `source_name` | 文章地址与来源标识 |
| `list_title` | 列表页上的标题（和详情页标题可能略有差异，便于比对） |
| `matched_keywords` | 命中的关键词（`--no-filter` 时为空） |
| `_body_source` | 正文是用哪个选择器取到的（`auto:xxx` 表示走了自动兜底，需要留意） |
| `meta.per_source_stats` | 每源：候选数 / 抓取数 / 命中数 / 错误，排查用 |

## 源的实测情况（截至配置当天，选择器都按真实页面探过）

| 源 | 引擎 | 列表容器 | 正文容器 | 备注 |
|---|---|---|---|---|
| 国防部网 | static | `ul.news-list li a` | `.article-content` | 证书与域名不匹配 → `verify:false` |
| 国防部·记者会专题 | static | `ul.news-list li a`、`ul.rich-news-list li a` | `.article-content` | 静态偶发 SSL 被掐 → 自动降级浏览器 |
| 中国军网 | static | `ul.news-list li a` | `#article-content` | 同样需要 `verify:false` |
| 外交部 | static | `a` + `link_re` 卡 `t<日期>_<id>.shtml` | `#News_Body_Txt_A` | 文章 URL 形态固定，用正则最稳 |
| 中国海警局 | static | `ul.text-news li a` | `.trs_editor_view` | TRS 编辑器正文 |
| 海警局·维权执法 | static | `div.tit-wrap p.title a` 等 | `.trs_editor_view` | 用 `/wqzf/`（带 `index.html` 的地址会超时）；有 24 页分页 |
| SCSPI | **browser** | `a` + `link_re` 卡 `/zh/<分类>/<时间戳>` | `div.field--name-body` | 静态 SSL/连接异常，必须浏览器 |
| 中国南海研究院 | static | `div#imgLIst a`、`div.contList h3 a` | `div.textHer` | 文章 URL 形如 `/info-detail/24/18473.html` |
| 新华网 | static | `div.list.list-txt ul li a` | `span#detailContent` | `h1` 常为空，标题靠 `title` 兜底 |
| 人民网 | static | `ul.list6.cf li a` 等三处 | `#rm_txt_zw` | 列表在 `www`、文章在 `politics` 等子域（同源判断已按子域放宽） |
| 央视网 | static | `div.text.hover a` 等 | `div.video_brief` | 视频页没有长正文，正文=节目简介 |
| 新华网·军事 | static | `p.name a`、`div.tit a`、`li a` | `span#detailContent` | 比新华网首页命中率高得多（首页排时政要闻） |
| 人民网·军事 | static | `h5 a`、`em a`、`li a` | `#rm_txt_zw` | 正文容器与人民网主站一致 |
| 新华网·国际 | static | `div.tit a`、`h3 a` | `span#detailContent` | 涉海/周边国际报道多 |

## 坑：国防部网/中国军网的 HTTPS 证书是错的

`https://www.mod.gov.cn/`（和 `https://www.81.mil.cn/`，两者解析到同一 IP `202.106.196.115`）
443 端口出示的证书是 **`CN=*.unionpayintl.com`（银联国际）**，与域名不匹配：

```
curl -s https://www.mod.gov.cn/            → curl: (60) SSL: no alternative certificate subject name matches
python requests.get("https://www.mod.gov.cn/") → SSLError: no alternative certificate subject name matches
```

任何**校验证书**的客户端在 TLS 握手阶段就失败，一个字节都拿不到（Hermes 自带的网页提取、
requests、Playwright 默认都属此类）。跳过校验后发现 https 还会 **301 跳到 http**，
即该站实际是 HTTP 服务。所以：

- 本配置里 `mod` / `mod_qwfb` / `mil81` 三个源都写了 `"verify": false`，并让 Scrapling 跟随跳转 → 正常抓到（实测 200，候选 40 篇）。
- 这不是反爬：连打 6 次请求全部 0.1s 内返回、假 UA 也照收，没有验证码/WAF 挑战/限流。

## 坑：国防部网的详情页标题带站点后缀，用"国防部"当关键词等于不过滤

详情页 `<title>` 形如 `XXX - 中华人民共和国国防部`，而关键词是在"标题+正文"里匹配的，
所以只要关键词表里有 `国防部`，该源**每一篇都会命中**（表现为"这个源 3/3 全命中"）。
要真正筛出涉海内容，用 `南海/台海/黄岩岛/仁爱礁/南部战区/执法巡查` 这类实词。

## 排除干扰：怎么区分"抓取失败"和"抓到了但被关键词筛掉"

看产物里 `meta.per_source_stats` 的四个数：

| 现象 | 候选 | 抓取 | 命中 | 错误 | 含义 |
|---|---|---|---|---|---|
| 抓取失败 | 0 | 0 | 0 | 有 | 网络/证书/选择器问题，`errors` 里有原因 |
| 抓到了没命中 | 40 | 5 | 0 | 0 | **抓取正常**，是关键词表太窄 |
| 正常 | 40 | 5 | 3 | 0 | — |

想确认正文抓没抓到，加 `--no-filter` 看原文：`python gov_news/crawl.py --sources mod --limit 5 --no-filter`


## 关于"命中率"（很重要）

关键词是在**每个源列表页的前 N 篇**里筛的，而聚合站（新华网/人民网/央视网首页）的列表顺序是
"时政要闻优先"，前几篇常常是领导人活动，所以会出现"抓到了但没命中"。这不是 bug，两种解法：

1. 提高 `--limit`（例如 `--limit 20`），把列表翻到更靠后的条目；
2. 把源换成**专题频道**——本配置已经这么做了（加了 `xinhua_mil` / `people_mil` / `xinhua_world`），
   这三个源在实测里 3 篇能命中 2~3 篇，而首页是 0 篇。

本次实测（14 源 × 3 篇 = 42 篇抓取，0 错误，筛后 25 篇）各源命中的差异就来自这里。

## 已知限制（下一步可以补）

1. **只抓列表首页**：海警维权执法有 24 页分页（`index_1.html`…），目前只抓第 1 页。要翻页在源里加 `page_urls` 或按 `index_{i}.html` 规律补。
2. **正文是纯文本**：图片、表格、附件（很多政府文告的 PDF 附件）没有保留，需要的话可另存 HTML。
3. **发布时间靠正则**：人民网这类把日期拆成多个节点的站点抓不到，需要给该源单独配 `date_selector`。
4. **央视网正文短**：视频页本来就没有长文本，如果目标是文字稿，得换成 news.cctv.com 的文章页。
