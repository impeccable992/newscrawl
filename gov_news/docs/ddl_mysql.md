# `gov_news` 入库表结构（Doris 样本 → MySQL 8.0 映射）

来源：`gov_news/db.py` 的 `ensure_schema()` 每轮跑 `CREATE TABLE IF NOT EXISTS`。
Doris 原样本存档在 [`ddl_doris_reference.sql`](ddl_doris_reference.sql)，**只换方言，字段名/顺序/注释不动**。

## 表与库

| 项 | 值 |
|---|---|
| 库 | `newscrawl`（`--db-name` 可覆盖；`CREATE DATABASE IF NOT EXISTS` + `utf8mb4` / `utf8mb4_general_ci`） |
| 表 | `ods_<source_key>_news`（每源一张） |
| `source_key` 约束 | 必须匹配 `^[a-z0-9_]+$`，否则 `ValueError` 拒绝建表（不静默转写、不猜） |
| 引擎 | `InnoDB`，`DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci` |
| 表注释 | 源的显示名（`source_name`，单引号与反斜杠做转义） |
| 主键 | `doc_id` = `md5(url)`，`varchar(64) NOT NULL` |

`connect()` 刻意不指定 database：库还不存在时它也不能报错，建库是 `ensure_schema()` 的职责。

## 字段映射

| # | 字段 | Doris 样本 | MySQL 8.0 | 注释 |
|---|---|---|---|---|
| 1 | `doc_id` | `varchar(64) NULL` | `varchar(64) NOT NULL` + **PRIMARY KEY** | `md5(url)` |
| 2 | `source` | `varchar(32) NULL` | `varchar(32) NOT NULL` | 源 key |
| 3 | `publish_date` | `date NULL` | `date NULL` | 发布日期（只到天） |
| 4 | `title` | `varchar(512) NULL` | `varchar(512) NULL` | 标题（采集侧已 `strip_html`） |
| 5 | `url` | `varchar(512) NULL` | **`varchar(1024) NULL`** | 原文链接（放宽，已确认） |
| 6 | `column_name` | `varchar(128) NULL` | `varchar(128) NULL` | 栏目/频道 |
| 7 | `publisher` | `varchar(256) NULL` | `varchar(256) NULL` | 发布方/来源媒体 |
| 8 | `summary` | `text NULL` | `text NULL` | 摘要 |
| 9 | `content` | `text NULL` | **`longtext NULL`** | 正文（放宽，避免 64KB 截断） |
| 10 | `content_length` | `int NULL` | `int NULL` | 正文字数 |
| 11 | `match_level` | `varchar(16) NULL` | **`varchar(32) NULL`** | 命中级别（放宽，已确认） |
| 12 | `matched_keywords` | `varchar(1024) NULL` | `varchar(1024) NULL` | 命中关键词，逗号分隔（采集侧给的是 list） |
| 13 | `source_page` | `varchar(512) NULL` | `varchar(512) NULL` | 来源页面/检索词页 |
| 14 | `raw_path` | `varchar(512) NULL` | `varchar(512) NULL` | 原始快照路径 |
| 15 | `http_status` | `int NULL` | `int NULL` | 详情页 HTTP 状态码 |
| 16 | `crawl_time` | `datetime NULL` | `datetime NULL` | 该条实际入库时刻（模块内 `datetime.now()`） |
| 17 | `batch_time` | `datetime NULL` | `datetime NULL` | 批次时间（调用方一轮传一次） |
| 18 | `image_urls` | `array<text> NULL` | **`JSON NULL`** | `图片 URL 数组(限量3张，存外链)`；`JSON` 无字符集属性，`SHOW CREATE TABLE` 里不会出现 `COLLATE` |

**只换方言 + 三处已确认的放宽**（`url` 512→1024、`match_level` 16→32、`content` `text`→`longtext`），其余字段与样本的类型完全一致。
差异仅限这三处：任何字段**不得比样本更窄**（曾出现 `publisher varchar(128)`、`summary varchar(2048)`、`matched_keywords varchar(512)`、`raw_path varchar(255)`、`publish_date datetime`，均已按样本收回；2026-11-20 修正）。

Doris 侧没有主键约束（`UNIQUE KEY` + `enable_duplicate_key=false` 由样本的写法推定），
MySQL 侧靠 `doc_id` 主键 + `INSERT ... ON DUPLICATE KEY UPDATE` 得到同样的幂等语义。

## 采集记录 → 列的取值规则

字段名以 `db.py` 的 `_normalize()` 为准；**缺键按空值处理，不抛 `KeyError`**。

| 来源字段 | 目标列 | 规则 |
|---|---|---|
| `url`（或 `link`/`detail_url`） | `url` + `doc_id` | 空 → 该条记 `skipped` 并写入 `errors`，不落库 |
| `source` | `source` | 缺省用调用方传的 `source_key` |
| `published`（或 `publish_date`/`pub_date`/`date`） | `publish_date` | 宽容解析：`2026-01-30 15:04:05` / `2026-01-30T15:04:05` / `2026年1月30日` / `2026-01-30` / `2026/01/30` / `20260130`；**解析不出写 NULL，不抛异常** |
| `title`（或 `list_title`） | `title` | 原样存（采集侧已清 `<em>`） |
| `body`（或 `content`） | `content` | 原样存 |
| `body_length`（或 `content_length`） | `content_length` | 转不出 int 时退回正文实际长度 |
| `summary`（或 `abstract`） | `summary` | 原样 |
| `category`（或 `column_name`/`channel`） | `column_name` | 原样 |
| `publisher`（或 `source_media`/`author`） | `publisher` | 原样 |
| `matched_keywords`（或 `keywords`/`keyword_hits`） | `matched_keywords` | **list → 逗号串** |
| `match_level`（或 `level`） | `match_level` | 原样 |
| `source_page`（或 `search_page`/`page_url`） | `source_page` | 原样 |
| `raw_path`（或 `snapshot_path`） | `raw_path` | 原样 |
| `http_status`（或 `status`） | `http_status` | 转不出 int 写 NULL |
| `image_urls`（或 `images`） | `image_urls` | list → JSON 数组，**只取前 3 条**；空 → NULL |
| `search_keyword` | — | **没有对应列**，只在 `matched_keywords` 里体现 |

文本列统一「空串 → NULL」：缺值和空值不混用，否则「没采到」和「采到空」分不清。
`upsert_records()` 返回 `{"inserted", "updated", "skipped", "errors"}`。

## 幂等与更新语义

```sql
INSERT INTO `newscrawl`.`ods_<key>_news` (<18 列>) VALUES (...)
ON DUPLICATE KEY UPDATE
  `publish_date`=VALUES(`publish_date`), `title`=VALUES(`title`), `summary`=VALUES(`summary`),
  `content`=VALUES(`content`), `content_length`=VALUES(`content_length`),
  `matched_keywords`=VALUES(`matched_keywords`), `match_level`=VALUES(`match_level`),
  `http_status`=VALUES(`http_status`), `image_urls`=VALUES(`image_urls`),
  `crawl_time`=VALUES(`crawl_time`), `batch_time`=VALUES(`batch_time`), ...
```

- 更新列表**不含** `doc_id` / `source` / `url`：同一 URL 重复入库时这三个保持原值。
- `inserted` / `updated` 由 MySQL 的 `affected_rows` 区分（新插 1、命中主键 2）。
  注意 MySQL 的既有语义：**命中了但所有被更新的列值都没变时 `affected_rows` 记 0**，
  这种情况本模块按「更新」计入 `updated`（不改变「行数不涨」这个关键结论）。
- 一轮采集共用一个由调用方传入的 `batch_time`（`crawl.py` 传的是
  `datetime.now().isoformat(timespec="seconds")` **字符串**，模块内解析成 `datetime` 再写库，
  解析不出写 NULL——不会因为把字符串塞进 DATETIME 列而整批插入失败）。

## 复现

```powershell
# 建表 + 幂等实跑（假记录，含 2 条同 URL）
uv run --isolated --extra ai --with pymysql python gov_news/tests/_manual_accept.py

# 只看 SQL，不连库
uv run --isolated --extra ai --with pymysql python gov_news/db.py --from-json <dump.json> --dry-run

# 表结构
& 'D:\software\mysql\bin\mysql.exe' -h 127.0.0.1 -P 3306 -uroot -p<密码> -e "SHOW CREATE TABLE newscrawl.ods_people_news\G"
```
