# gov_news 14 个源 · 站内检索（按标题）入口调研

- 调研日期：2026-11-20
- 目标：为 `gov_news/sources.json` 的 14 个源逐个确定**站内检索（按标题搜索）**的可用路径
- 范围：**只做调研**。本轮不改 `crawl.py`、不改 `sources.json` / `keywords.json`、不动 `scrapling/`、不提交、不建分支
- 探测脚本：`gov_news/out/research/probe_*.py`（临时产物，`out/` 已被 .gitignore 忽略）
- 配置草案：[`search_config_draft.json`](../out/research/search_config_draft.json)

统一跑法（仓库根）：

```powershell
$env:UV_DEFAULT_INDEX='https://pypi.tuna.tsinghua.edu.cn/simple'
[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'
uv run --isolated --extra ai python gov_news/out/research/probe_25_verify_all.py
```

> 代理纪律（项目所有者确认）：`http://127.0.0.1:7897` **只给需要翻墙的站点**。本报告里 14 个源中只有 `scspi` 需要代理；其余（全部 `.gov.cn` / `news.cn` / `people.com.cn` / `cctv.com` / `nanhai.org.cn`）一律**不加代理**，命令里也不带 `--proxy`。

---

## 0. 结论总表（先看这个）

| # | key | 检索入口类型 | 按标题参数 | 需浏览器 | 需代理 | 证据强度 |
|---|---|---|---|---|---|---|
| 1 | `mod` | **api** | `title` | 否 | 否 | **实测通过** |
| 2 | `mod_qwfb` | **api**（复用 mod 后端） | `title` | 否 | 否 | **实测通过** |
| 3 | `mil81` | **api**（自有后端 `mil-search.81.cn`） | `title` | 否 | 否 | **实测通过** |
| 4 | `fmprc` | **browser**（IRS 平台） | `searchBy=title` | 是 | 否 | 部分（接口定位到，凭证走不通） |
| 5 | `ccg` | **none** | — | — | — | 实测（确认不可用，后端 404） |
| 6 | `ccg_wqzf` | **none** | — | — | — | 实测（同 ccg） |
| 7 | `scspi` | **results_page** | `keywords`（仅全文） | 否 | **是** | **实测通过** |
| 8 | `nanhai` | **none** | — | — | — | 实测（确认表单是空壳） |
| 9 | `xinhua` | **browser**（`so.news.cn`） | `searchFields` | **是**（静态 403） | 否 | 部分（检索接口 + 响应结构实测通过；翻页上限/DOM 选择器未验证） |
| 10 | `people` | **api** | `hasTitle=true` | 否 | 否 | **实测通过** |
| 11 | `cctv` | **browser** | 无 | 是 | 否 | 部分（有总条数，取不到结果列表） |
| 12 | `xinhua_mil` | **api**（复用 `xinhua` + 前缀过滤） | 同 xinhua | 是 | 否 | 部分 |
| 13 | `people_mil` | **api**（复用 `people` + 域名过滤） | `hasTitle=true` | 否 | 否 | 部分 |
| 14 | `xinhua_world` | **api**（复用 `xinhua` + 前缀过滤） | 同 xinhua | 是 | 否 | 部分 |

一句话总结：**4 个源可以走纯静态 JSON 接口（`mod` / `mod_qwfb` / `mil81` / `people`）**，
**3 个复用父站接口但要浏览器（`xinhua` / `xinhua_mil` / `xinhua_world`，接口是 `so.news.cn/getNews`，静态恒 403）**，
**1 个复用父站接口且纯静态（`people_mil`，复用 `people` 接口 + 域名过滤）**，
**1 个走静态结果页（`scspi`，但必须代理）**，
**2 个必须浏览器且当前拿不到结果（`fmprc` 缺凭证、`cctv` 取不到结果列表）**，
**3 个确认没有可用站内检索（`ccg` / `ccg_wqzf` / `nanhai`）**。

---

## 1. `mod`（国防部网）

- **类型**：`api`
- **接口**：`GET http://mod-search.mod.gov.cn/api-surface/es/docSearchEasy`
- **按标题参数**：`title={keyword}`（页面表单 `searchfield=TITLE` → `title`；`searchType` 1=标准 / 2=模糊 / 3=二次检索）
- **全部必填参数**：`pageNumber`、`pageSize`、`returnFields`、`indexNames=manuscript`、`highlightType=2`、`title`、`searchType=2`、`channelId=718`
- **翻页与上限**：`pageNumber` 从 1 起，`pageSize=10`；实测 page=1/2/3 返回不同内容。关键词「南海」时 `total=430` → 43 页够全量
- **结果字段**：`data.resultList[].title` / `.manuscriptData.url` / `.issueTime`（秒级时间戳）/ `.manuscriptData.classify_name`；`data.total`
- **URL 正则**：`mod\.gov\.cn/.+\d{6,}\.html$`
- **所需条件**：`verify=false`（证书 CN 与域名不匹配）；不需浏览器；**不需代理**
- **证据（实测通过）**：

```
=== mod: GET http://mod-search.mod.gov.cn/api-surface/es/docSearchEasy (channelId=718) ===
  status: 200
  total: 430
  got: 10
  sample:
    - 菲方不能任由一小撮人肆意妄为在南海挑衅生事 | http://www.mod.gov.cn/gfbw/xwfyr/lxjzhzt/2026lxjzh/2026n10y/16497142.html
```

补充证据（`probe_07_channels.py`，同一后端不同 channelId 的归属）：

```
channelId=718  code=200 total=430  channel=国防部网
channelId=636  code=200 total=566  channel=中国军网
channelId=717  code=200 total=13   channel=解放军画报
channelId=716  code=200 total=232  channel=八一视频
channelId=719/637/635/720/638/700/...  code=None（空响应，非法 channelId）
```

**顺带纠正一个卡上的说法**：站点自己的检索页 `http://www.mod.gov.cn/zjlm/ssjgym/index.html` 实测 **200 但只有 7024B 空骨架、0 条结果链接**——它是 JS 填的，不能直接抓；只有接口可用。

---

## 2. `mod_qwfb`（国防部·记者会专题）

- **类型**：`api`（**与 `mod` 同一后端、同一 channelId=718**）
- **接口与参数**：同 `mod`，逐字相同
- **按标题参数**：`title`
- **翻页与上限**：同 `mod`
- **URL 正则**：`mod\.gov\.cn/gfbw/.+\d{6,}\.html$`（接口没有"只搜记者会栏目"的参数，复用 718 后按 URL 前缀过滤）
- **所需条件**：同 `mod`（`verify=false`，无浏览器，**无代理**）
- **证据**：

```
=== mod: GET http://mod-search.mod.gov.cn/api-surface/es/docSearchEasy (channelId=718) ===
  status: 200   total: 430   got: 10
    - 菲方不能任由一小撮人肆意妄为在南海挑衅生事 | http://www.mod.gov.cn/gfbw/xwfyr/lxjzhzt/2026lxjzh/2026n10y/16497142.html
```

（样例 URL 落在 `/gfbw/` 下，正则能命中。）

- **未验证**：`channelId=718` 是否还有"只搜 `/gfbw/`"的子频道号——没有找到 channelId 列表接口。

---

## 3. `mil81`（中国军网）

- **类型**：`api`
- **接口**：`GET http://mil-search.81.cn/api-surface/es/docSearchEasy`
- **关键发现**：**军网有自己独立的检索后端 `mil-search.81.cn`，不是 `mod-search`**。来源是它检索页引用的 `http://www.81.mil.cn/template/tenant207/t511/js/index.js`：

```js
url: document.location.protocol + '//mil-search.81.cn/api-surface/es/docSearchEasy',
// ...
channelId = param['channelId'] ? param['channelId'] : 636
```

- **按标题参数**：`title={keyword}`（`searchfield='TITLE'` 分支）
- **全部必填参数**：`pageNumber`、`pageSize`、`returnFields`、`indexNames=manuscript`、`highlightType=2`、`title`、`searchType`、`channelId=636`
- **翻页与上限**：`pageNumber` 从 1 起，`pageSize=10`；实测 page=1/2/3 各 10 条、首条互不相同。`total=566`（`searchType=2`）/ `2601`（`searchType=1`）→ 模糊匹配时 57 页
- **URL 正则**：**`81\.(mil\.cn|cn)/.+\d{6,}\.html$`** —— 注意接口返回的是 `www.81.cn`，不是 `81.mil.cn`
- **所需条件**：`verify=false`；不需浏览器；**不需代理**
- **证据（实测通过）**：

```
=== mil81: GET http://mil-search.81.cn/api-surface/es/docSearchEasy (channelId=636) ===
  status: 200
  total: 566
  got: 10
  sample:
    - 天天学习丨中南海月刊（2026.10） | http://www.81.cn/xx_207779/16498330.html
    - 【双语】国防部：中方将以有力行动遏制任何祸乱南海的行径 | http://www.81.cn/yw_208727/16493579.html
```

交叉验证（`probe_09`）：同一 `mil-search` 接口传 `channelId=718` 返回的是 `channel=国防部网` 的结果，说明该后端按 channelId 分站，`636` 才是军网。

---

## 4. `fmprc`（外交部）—— 部分验证

- **类型**：`browser`（IRS 检索平台）
- **检索页 URL 模板**：`https://www.fmprc.gov.cn/irs-c-web/search.shtml?code=17e50b77dab&searchBy=title&searchWord={keyword}`
  （`code=17e50b77dab` 来自外交部首页内联 JS；`searchBy=title` 即按标题）
- **实测结果（关键）**：GET 该 URL → **200，但服务端只渲染出 0 条**：

```
=== mfa_irs / fmprc_irs（带完整浏览器头）===
  status: 200   bytes: 55724   title: 搜索结果 — 中华人民共和国外交部
  mentions: 0   links: 0
  TXT: ... 已为您找到约 0 条相关结果 ... 很抱歉！未能搜到 ...
```

- **真正的结果接口（已定位）**：`POST https://www.fmprc.gov.cn/irs/front/search`，body 需带 `code`。逐步逼近过程：

```
POST /irs/front/search  body(无 searchRequestId)
→ 200 {"success":false,"msg":"searchRequestId 必须是 UUID","data":null,"code":1002}

POST /irs/front/search  body(补 UUID, dataTypeId="1")
→ 200 {"success":false,"msg":"根据数据分类ID【1】未找到数据分类！","code":1000}

再请求 → 200 {"success":false,"msg":"凭证无效或已过期","code":1004}
```

- **结论**：**接口存在、无需代理，但缺 `searchRequestId` 语义与有效凭证，静态方式拿不到结果**。
- **所需条件（推测，未验证）**：真实浏览器完成 IRS 的 token/凭证流程后，结果列表才会填充。
- **未验证项 + 原因 + 需要什么条件**：
  - 未验证：浏览器里能否拿到结果 —— 原因是短时间内多次请求后 `https://www.mfa.gov.cn/irs-c-web/search.shtml` 对浏览器请求也返回 **403 Forbidden（2106B）**（疑似临时风控/限流）。
  - 需要：等限流窗口过去后，用 `StealthySession(record_requests=True)` 打开该检索页并抓 `session.network`，从 XHR body 里读出 `searchRequestId` 的生成方式与凭证头。

---

## 5. `ccg`（中国海警局）—— 确认**不可用**

- **类型**：`none`
- **检索入口是存在的，但整条链路是坏的**，证据链三段：

1. 首页检索按钮的跳转目标（`gov_news/out/research/dump_ccg_wap-nav.js`）：

```js
$('.search-button').on("click", function () {
    var q = $.trim($('.search-keywords').val()) || $.trim($('.search-keyword').val());
    if (q) { window.location = 'https://www.ccg.gov.cn/js/index.html?q=' + q; }
    else { alert("搜索关键词不能为空"); }
    return false
});
```

2. 跳转目标页 `GET https://www.ccg.gov.cn/js/index.html?q=南海` → **200，但结果数恒为 0**：

```
=== ccg: GET https://www.ccg.gov.cn/js/index.html?q=南海 ===
  status: 200
  page_title: 检索
  total_span: 0
```

3. 该页内联脚本用的后端是 `POST /irs/front/search`（`code=18f7c368b26`），**静态重试 3 次 + 真实浏览器点击检索按钮 3 次，全部 404**：

```
[www_irs_front try1..3]  POST https://www.ccg.gov.cn/irs/front/search  → 404 / 404 / 404
[api_irs_front try1..3]  https://api.ccg.gov.cn → DNS 不解析（Could not resolve host）

=== ccg_ui（真实浏览器点检索按钮 + 点"标题"） ===
  [ccg_ui] 200 7074B total_text='0' mentions=0 links=0
    NET POST 404 https://www.ccg.gov.cn/irs/front/search
        BODY {"searchWord":"南海",...,"searchBy":"all","pageNo":1,"pageSize":10}
    NET POST 404 https://www.ccg.gov.cn/irs/front/search
        BODY {...,"searchBy":"title",...}
```

4. 补充：`search.ccg.gov.cn` / `so.ccg.gov.cn` / `sousuo.ccg.gov.cn` **DNS 全部不解析**；`/search/`、`/search.html`、`/plus/search.php` 等 11 条候选路径均 404 或超时（`probe_14` / `probe_16`）。
- **怎么确认"没有"**：不是靠猜——是"入口存在 → 走完它自己的跳转 → 后端 404 + 结果数 0"，且用真实浏览器复现了同样的 404。
- **所需条件**：需海警局修好 IRS 后端；在那之前该源只能走列表页抓取。
- ⚠️ **踩过的假阳性（记录以免重踩）**：`https://www.ccg.gov.cn/?m=search&c=index&a=init&siteid=1&typeid=&q=南海` 会返回 200/57359B 且页内有「南海」——但那**就是首页原文**（"南海"命中的是首页新闻标题《外籍货轮南海失去动力…》），与首页字节数完全一致（57359B），`?q=` 根本没被服务端处理。

---

## 6. `ccg_wqzf`（海警局·维权执法）

- **类型**：`none`
- 站点级检索就是 `ccg` 那一个（且不可用，见上）；栏目页 `https://www.ccg.gov.cn/wqzf/` 实测无 form、无 search 链接（`probe_01`：`forms=0 search_links=1`，唯一命中是首页自身链接）。
- **怎么确认**：同 `ccg` 的证据链 + 栏目页无独立检索入口。
- **建议**：该源继续走列表页抓取（它本来就有 24 页 `index_1.html…`）。

---

## 7. `scspi`（南海战略态势感知）

- **类型**：`results_page`
- **结果页**：`GET https://www.scspi.org/zh/sitesearch?keywords={keyword}`
- **按标题参数**：只有 `keywords`（站点简单检索，**全文匹配，没有仅标题选项**）；另有 Drupal 高级检索 `/zh/search/node`（POST，需 `keys`/`op`/`form_build_id`），参数繁琐，不推荐
- **代理（本卡重点，实测两次）**：

```
=== 不加代理 vs 加代理（probe_04）===
[home         no_proxy  ] FAILED Timeout: curl: (28) Operation timed out after 20006 ms with 0 bytes received
[home         with_proxy] 200   35078B  1.63s  title=南海战略态势感知计划 | 促进南海的透明、和平与合作
[search_cn    no_proxy  ] FAILED Timeout: curl: (28) ... 20013 ms
[search_cn    with_proxy] 200   19197B  1.70s  title=搜索 | 南海战略态势感知计划
[search_node  no_proxy  ] FAILED SSLError: curl: (35) Recv failure: Connection was reset
[search_node  with_proxy] 200   19197B  0.76s  title=搜索 | 南海战略态势感知计划
```

**结论：只有 `--proxy http://127.0.0.1:7897` 能通；不加代理 3/3 失败。**

- **翻页**：未验证（结果页未观察到明确分页参数）
- **URL 正则**：`scspi\.org/zh/(yjbg|dtfx|zdhd|militaryposture|maritimepolicies|marinedevelopment|rulesandorder)/\d+`
- **所需条件**：`verify=true`；**不需浏览器**；**必须代理**
- **证据（实测通过）**：

```
=== scspi: GET https://www.scspi.org/zh/sitesearch?keywords=南海 (proxy=127.0.0.1:7897) ===
  status: 200
  got: 15
  sample:
    - 构建 南海 新叙事：为 南海 和平、稳定与合作正本清源 | /zh/dtfx/1752823415
    - 中国视角看 南海 | /zh/dtfx/1730736113
    - 没有国家能单独控制 南海 ，也没有国家会失去 南海 | /zh/dtfx/1553014800
```

（注意：结果链接是**相对路径** `/zh/...`，实现时要按结果页 URL 补全成绝对地址。）

---

## 8. `nanhai`（中国南海研究院）—— 确认**不可用**

- **类型**：`none`
- 首页和内页都**有**检索表单（`probe_01`）：

```
  form action=/search.html method=get
      input text   name= keywords   value= 搜索......
      input submit name=            value=
```

- 但**服务端不产出任何结果条目**：

```
=== nanhai: GET https://www.nanhai.org.cn/search.html?keywords=南海 ===
  status: 200
  bytes: 13253
  info_detail_links: 0        # 站内文章链接（info-detail/xx/xxxxx.html）数量 = 0

[nanhai kw_other] 200  4880B  mentions=0  info_links=0     # 换『量子计算』同样 0 条
```

- 页面本身**没有任何检索脚本**（`probe_06`：`scripts=[]`），也没有外部检索客户端接入，因此不是"JS 没渲染出来"，而是服务端就没这条能力。
- **怎么确认"没有"**：拿两个关键词（南海 / 量子计算）各请求一次，页内文章链接数都是 0；再确认页面无检索 JS。
- **所需条件**：需站点自行实现；那之前该源只能走列表页抓取。

---

## 9. `xinhua`（新华网）

- **类型**：`browser`（静态被 403 拦）
- **检索页**：`https://so.news.cn/`（React SPA；静态只有 761B 空壳，`./static/index-*.js`）
- **按标题参数**：`searchFields`（0=默认；页面还有"标题/全文"选择）；另有 `lang`、`sortField`
- **底层接口（已从 bundle 里扒出并实测到响应）**：

```
GET https://so.news.cn/getNews?lang=cn&curPage=1&searchFields=0&sortField=0&keyword=南海
```

- **为什么必须浏览器**：该接口**静态直调恒 403**（带完整浏览器头 + Referer 也是 403）：

```
[xinhua with_headers] 403 561B  <title>403 Forbidden</title>  (openresty/1.27.1.1)
[xinhua plain       ] 403 561B  <title>403 Forbidden</title>
```

只有由页面自身发起才能过。用 `StealthySession` 打开首页、在输入框填词回车后，抓到的 XHR 是：

```
=== xinhua_ui（StealthySession + record_requests） ===
  final_url: https://so.news.cn/#search/0/南海/1/0
    NET GET 200 https://so.news.cn/getNews?lang=cn&curPage=1&searchFields=0&sortField=0&keyword=%E5%8D%97%E6%B5%B7
        RESP {"code":200,"content":{"keyword":"南海","sortField":"0","curPage":1,
              "results":[{"contentId":"...","pubtime":"2026-11-20 10:29:48","sitename":"首页Pro",
                          "title":"沈仲文：从深圳再出发，亚太合作鹏程万里",
                          "url":"https://www.news.cn/20261120/e8d6319f04b84437b0a087d8d7af2fb5/c.html"}, ...]}}
```

- **响应结构**：`content.results[]`（`title` / `url` / `pubtime` / `sitename` / `des`）、`content.resultCount`、`content.pageCount`
- **翻页与上限**：`curPage`；结果页 URL 形如 `#search/0/<词>/<页码>/0`。**上限未验证**
- **URL 正则**：`news\.cn/.+/c\.html$`
- **所需条件**：`verify=true`；**必须浏览器**；**不需代理**
- **未验证项**：结果 DOM 的稳定选择器（React 渲染，类名带 hash）、`resultCount` 具体数值与翻页上限。

---

## 10. `people`（人民网）

- **类型**：`api`（静态 POST 直调成功）
- **接口**：`POST http://search.people.cn/search-platform/front/search`，`Content-Type: application/json;charset=UTF-8`
- **按标题参数**：**`hasTitle: true`**（配 `hasContent` / `isFuzzy`）
- **请求体（实测抓自它自己的 XHR）**：

```json
{"key":"南海","page":1,"limit":10,"hasTitle":true,"hasContent":true,"isFuzzy":true,
 "type":0,"sortType":2,"startTime":0,"endTime":0,"belongsId":[]}
```

- **响应结构**：`data.records[]`（`title` / `url` / `inputTime` / `content` / `belongsName` / `domain`）、`data.total`
- **翻页与上限**：`page` 从 1 起；`limit=50` 时返回 50 条（服务端接受大 limit）；关键词「南海」`total=10052`（全人民网系）
- **URL 正则**：`people\.com\.cn/n\d/\d{4}/\d{4}/c\d+-\d+\.html$`
- **额外请求头**：`Origin: http://search.people.cn`、`Referer: http://search.people.cn/s/`（缺了也能过，但建议带上）
- **所需条件**：`verify=true`；**不需浏览器**；**不需代理**
- **证据（实测通过）**：

```
=== people: POST http://search.people.cn/search-platform/front/search (hasTitle=true) ===
  status: 200
  total: 10052
  got: 10
  sample:
    - 南海“长征” | http://pic.people.com.cn/n1/2026/1022/c1016-40819484.html
    - 南海安全圆桌对话暨《南海仲裁案裁决新批驳》发布会在香港举行 | http://gba.people.cn/n1/2026/0714/c42272-40760391.html
```

- **注意**：实测 `type=0` 与 `type=1`、以及 `belongsId` 传值**都不改变结果**，所以频道筛选（人民网·军事）请改用**最终 URL 的域名**过滤。

---

## 11. `cctv`（央视网）—— 部分验证

- **类型**：`browser`
- **结果页**：`GET https://search.cctv.com/search.php?qtext={keyword}&type=web`（`type` 还有 `video/news/all`，**实测四种表现相同**）
- **按标题参数**：**没有**（单一 `qtext`，不区分标题/全文）
- **能拿到的**：页面文本里有总条数——

```
[海警] status=200 bytes=49914  → TXT: ... 《海警》全部网页结果共2983条
[南海] status=200 bytes=55330  → TXT: ... 《南海》全部网页结果共17535条
```

- **拿不到的**：**结果列表**。静态 HTML 里没有结果条目（只有 `var playlistArray=new Array()` 空数组与模板链接）；`type=web` 时页面通过 `getPlaylist('p36710', ...)` 去取
  `https://r.img.cctvpic.com/so/cctv/list/8/p36710.js`，但那个 JS **只有 1 条记录**且只在个别词（南海）出现，**不能当检索列表用**：

```
[cctv type=web] playlist=1837B records=1
    title: 南海风云 [深度国际] 20110624
    url  : https://tv.cctv.com/2011/06/25/VIDE1355587158307441.shtml
[海警] playlist 不存在（playlist=False）
```

- 用 `StealthySession` 渲染该检索页后**也没拿到结果条目，且抓不到取数 XHR**：

```
[cctv 海警] 200 131639B mentions=50 links=10    # links 全是导航/推荐位，不是检索结果
    （network 里没有结果类 XHR）
```

- **URL 正则**：`tv\.cctv\.com/\d{4}/\d{2}/\d{2}/VID[A-Z0-9]+\.shtml$`（注意实际有 `VIDA...` 与 `VIDE...` 两种，原配置只写了 `VIDE`，会漏）
- **所需条件**：`verify=true`；需浏览器；**不需代理**
- **未验证项 + 原因 + 需要什么条件**：结果列表的取数接口没找到（页面里没暴露、`record_requests` 也没捕到）。需要先用浏览器 DevTools 人工看一次该页 Network，或确认该检索页是否要求登录/带特定 cookie。
- **建议**：该源**继续走列表页抓取**，不要为它做站内检索。

---

## 12. `xinhua_mil`（新华网·军事）

- **类型**：`api`（**复用 `xinhua` 的接口 + 前缀过滤**）
- **栏目页实测**：`https://www.news.cn/milpro/index.htm` → **200，forms=0，search_links=0**（`probe_01`）→ 栏目自身没有检索入口
- **做法**：调 `so.news.cn/getNews`，再用 URL 前缀 `https://www.news.cn/milpro/` / 正则 `news\.cn/milpro/.+/c\.html$` 过滤
- **按标题参数 / 翻页**：同 `xinhua`（`searchFields` / `curPage`）
- **所需条件**：同 `xinhua`（**必须浏览器**，静态 403；不需代理）
- **证据强度**：部分 —— 父站检索链路实测通过；**"频道过滤后还剩多少条"未实测**。
- **未验证项**：接口是否有频道参数（未找到）；前缀过滤的实际命中率。

---

## 13. `people_mil`（人民网·军事）

- **类型**：`api`（**复用 `people` 的接口 + 域名过滤**）
- **栏目页实测**：`http://military.people.com.cn/` → 200，有一个检索 form：

```html
<form action="http://search.people.cn/getNewsResult" method="get">
  <input type="hidden" name="channel" value="military" />
  <input type="text"   name="keyword" value="新闻检索" />
</form>
```

- 但**实测 `getNewsResult` 返回的是 SPA 外壳而不是结果**：`200 / 2948B`，内容与 `search.people.cn` 首页的 Nuxt 外壳一致（`<title>人民网-搜索</title>`）。
- **做法**：改用 `POST /search-platform/front/search`（`hasTitle=true`），再按 `military.people.com.cn` 域名过滤
- **按标题参数**：`hasTitle=true`；**翻页**：`page`
- **URL 正则**：`military\.people\.com\.cn/n\d/\d{4}/\d{4}/c\d+-\d+\.html$`
- **所需条件**：`verify=true`；不需浏览器；**不需代理**
- **证据强度**：部分 —— 父站接口实测通过（total=10052）；`belongsId` 频道过滤未验证（传值不生效，需用域名过滤）。
- **未验证项**：`channel=military` 在这个接口里对应的 `belongsId` 值（如果将来想用它替代域名过滤）。

---

## 14. `xinhua_world`（新华网·国际）

- **类型**：`api`（**复用 `xinhua` 的接口 + 前缀过滤**）
- **栏目页实测**：`https://www.news.cn/world/index.html` → **200，forms=0，search_links=0** → 栏目自身没有检索入口
- **做法**：`so.news.cn/getNews` + URL 前缀 `https://www.news.cn/world/` / 正则 `news\.cn/world/.+/c\.html$`
- **所需条件**：同 `xinhua`（必须浏览器；不需代理）
- **证据强度**：部分（同 `xinhua_mil`）

---

## 15. 横向：共用检索后端一览（对实现最有用的一张表）

| 后端 | 覆盖的源 | 鉴权 | 备注 |
|---|---|---|---|
| `mod-search.mod.gov.cn/api-surface/es/docSearchEasy` | `mod`(718)、`mod_qwfb`(718)，另有 717=解放军画报、716=八一视频 | 无 | GET，`title=` 按标题 |
| `mil-search.81.cn/api-surface/es/docSearchEasy` | `mil81`(636) | 无 | GET，军网**自有**后端；返回 `www.81.cn` |
| `search.people.cn/search-platform/front/search` | `people`、`people_mil` | 无 | POST JSON，`hasTitle` |
| `so.news.cn/getNews` | `xinhua`、`xinhua_mil`、`xinhua_world` | 无 | GET；**静态 403，必须页面内发起** |
| IRS 平台 `/irs/front/search` | `fmprc`（`code=17e50b77dab`）、`ccg`（`code=18f7c368b26`，**404 不可用**） | fmprc 需凭证 / ccg 后端缺失 | POST JSON |
| Drupal `sitesearch` | `scspi` | 无 | GET，**必须代理** |

**通道型源的通用解法**：`xinhua_mil` / `xinhua_world` / `mod_qwfb` / `people_mil` 都没有独立检索，统一"**复用父站检索 + URL 前缀/域名过滤**"。

---

## 16. 未验证项汇总（附原因与需要什么条件）

| 源 | 未验证的事 | 原因 | 需要什么条件 |
|---|---|---|---|
| `fmprc` | 浏览器里能否拿到检索结果 | 多次请求后检索页对浏览器请求也返 403（疑似限流） | 等限流窗口过去；用 `StealthySession(record_requests=True)` 抓 XHR，读出 `searchRequestId` 生成方式与凭证头 |
| `fmprc` | `searchRequestId` 之外的凭证 | 接口报"凭证无效或已过期"(1004) | 同上 |
| `cctv` | 结果列表的取数接口 | 页面未暴露，`record_requests` 也没捕到结果类 XHR | 人工 DevTools 看一次 Network，或确认是否需登录/cookie |
| `cctv` | 翻页参数与上限 | 同上 | 同上 |
| `xinhua` | `resultCount` 数值、翻页上限、结果 DOM 选择器 | React 渲染类名带 hash；本次只验证到接口层 | 渲染后 dump DOM 再定选择器 |
| `scspi` | 翻页参数与上限 | 结果页没有明显分页控件 | 查 Drupal pager 参数 |
| `mod_qwfb` | 是否有"只搜 /gfbw/"的 channelId | 没找到 channelId 列表接口 | 找检索后台的频道配置，或用前缀过滤代替 |
| `xinhua_mil` / `xinhua_world` | 频道过滤的实际命中率 | 本次未跑过滤后的统计 | 跑一次带前缀过滤的统计 |
| `people_mil` | `channel=military` 对应的 `belongsId` | 传值不生效 | 抓一次带频道筛选的真实 XHR |
| 全部 | 实现侧"每词 5 页、上限 20"是否够 | 本轮只产配置草案，未跑实现 | 落 `sources.json` 后按 `--list-only` 验证 |

---

## 17. 对实现的影响（结论前置）

1. **可以直接做成 `engine: "api"` 的源（纯静态，最省）**：`mod`、`mod_qwfb`、`mil81`、`people`（共 4 条）。
   - `mil81` 记得用 `mil-search.81.cn` + `channelId=636`，并把 `link_re` 放宽到 `81\.(mil\.cn|cn)/...`。
2. **`crawl.py` 现有能力不够，落地前需先扩代码**（本轮**没改**）：
   - `people` 需要 **POST + JSON body + 自定义头**，现有 `fetch_api()` 只支持 GET + query 参数；
   - `people` 的 `title` 是 `hasTitle: true`（布尔语义）而不是 `title={keyword}`；
   - 频道型源需要"检索后再按 URL 前缀/域名过滤"的能力；
   - 建议形态：把 `api` 扩展成支持 `method` / `body` / `headers` / `filter`，而不是给每个源写分支。
3. **必须走浏览器的源**：`xinhua`（含 `xinhua_mil` / `xinhua_world`）、`fmprc`、`cctv`。
   - `xinhua` 系有价值（接口响应结构已确认、返回 `title/url/pubtime`），值得为它做 `browser` + `page_action`；
   - `fmprc` 在凭证问题解决前**不建议投入**；
   - `cctv` 取不到结果列表，**建议放弃站内检索**，继续用列表页。
4. **放弃站内检索的源**：`ccg`、`ccg_wqzf`、`nanhai`（后端缺失或空壳），继续走列表页。
5. **`scspi` 是唯一需要代理的源**，且代理必须可配（沿用 `--proxy` 参数，不写死）。
6. **`cctv` 的 `link_re` 有个现存小 bug**：当前只写了 `VIDE.+\.shtml`，实际站点同时有 `VIDA...`，建议放宽为 `VID[A-Z0-9]+`。

---

## 附：本报告用到的探测脚本（都在 `gov_news/out/research/`）

| 脚本 | 作用 |
|---|---|
| `probe_common.py` | 公用：带节流的静态/浏览器抓取（同站间隔 ≥1.2s，不并发） |
| `probe_01_recon.py` | 13 个首页/栏目页的 form 与 search 链接侦察 |
| `probe_02_candidates.py` | 候选检索接口/结果页首轮验证 |
| `probe_03_deep.py` | mil81/fmprc/ccg/xinhua/people/nanhai/cctv 深挖 |
| `probe_04_scspi_proxy.py` | **scspi 不加代理 vs 加代理对照** |
| `probe_06_followup.py` | mil81 检索 JS、fmprc IRS、ccg 路径、nanhai、xinhua bundle、people、cctv |
| `probe_07_channels.py` | `mod-search` 的 channelId 空间扫描 |
| `probe_09_mil81_scspi.py` | `mil-search.81.cn` 验证 + scspi 检索参数 |
| `probe_11_ccg_fmprc.py` | ccg 路径候选 + fmprc 带头复测 |
| `probe_12_nanhai_cctv.py` | nanhai 检索页性质 + cctv 翻页/结果块 |
| `probe_13_session_xhr.py` | `StealthySession` + `record_requests` 抓 XHR |
| `probe_14_ccg_search.py` | ccg 11 条候选检索路径 |
| `probe_16_final.py` | cctv 各 type、nanhai 双关键词、fmprc 带头、ccg form |
| `probe_17_people_api.py` | **people 接口静态直调验证** |
| `probe_18_ccg_xinhua.py` | ccg 检索 JS 与浏览器点击 |
| `probe_19_xinhua_ui.py` | **xinhua 走首页检索框抓 XHR** |
| `probe_21/22_ccg_irs*.py` | ccg IRS 接口重试 |
| `probe_23_ccg_ui.py` | ccg 真实浏览器点检索 |
| `probe_24_fmprc_ui.py` | fmprc 检索页浏览器复测 |
| `probe_26/27_fmprc_*.py` | **fmprc IRS POST 接口逼近（UUID → 凭证）** |
| `probe_25_verify_all.py` | **可复现性回归：各源最终方式的命令级证据** |
| `probe_29_cctv_xhr.py` | cctv 检索页渲染 + XHR |
