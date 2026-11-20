"""把 gov_news 的采集记录写进 MySQL 8.0（库 `newscrawl`，每源一张表 `ods_<source_key>_news`）。

设计要点
--------
1. **每源一表**：表名由源 key 拼出（`ods_<source_key>_news`），key 不合法（非 ``^[a-z0-9_]+$``）直接拒绝，
   不做静默转写——表名会进 SQL，宁可报错也不要靠猜。
2. **程序建库建表**：`ensure_schema` 负责 `CREATE DATABASE IF NOT EXISTS` + 每轮 `CREATE TABLE IF NOT EXISTS`，
   `connect()` 只连实例、不选库，所以库还不存在时也不会报错。
3. **幂等**：`doc_id = md5(url)` 是主键，写库走 `INSERT ... ON DUPLICATE KEY UPDATE`，
   只更新业务字段，`doc_id` / `source` / `url` 不动。
4. **批次**：一轮采集共用一个由调用方传入的 `batch_time`（字符串或 datetime 都收），
   `crawl_time` 是该条实际写库的时刻。
5. **驱动**：PyMySQL，由 uv 注入（`uv run --isolated --extra ai --with pymysql ...`），不进 pyproject 依赖。

字段名/顺序/注释对齐项目所有者给的 Doris 样本（见 docs/ddl_doris_reference.sql），只换方言，
映射说明见 docs/ddl_mysql.md。

用法（仓库根目录执行）
----------------------
    # 看 CLI 参数
    uv run --isolated --extra ai --with pymysql python gov_news/db.py --help

    # 只打印将要执行的 SQL，不连库
    uv run --isolated --extra ai --with pymysql python gov_news/db.py --from-json out/people.json --dry-run

    # 回灌入库
    uv run --isolated --extra ai --with pymysql python gov_news/db.py --from-json out/people.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DEFAULT_DB_NAME = "newscrawl"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3306
DEFAULT_USER = "root"
# 卡片定死的本机默认口令：可被 connect()/CLI 覆盖，且从不回显（见 --password 帮助与测试）
DEFAULT_PASSWORD = "123456"  # nosec B105

# 表名会被拼进 SQL，key 只允许小写字母/数字/下划线
_SOURCE_KEY_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")
# 库名同样只允许标识符字符（CLI 的 --db-name 是外部输入）
_DB_NAME_OK = _SOURCE_KEY_OK | frozenset("$")
_CHARSET = "utf8mb4"
_COLLATION = "utf8mb4_general_ci"

# 与 Doris 样本逐字对齐的字段名与顺序
COLUMNS: Tuple[str, ...] = (
    "doc_id",
    "source",
    "publish_date",
    "title",
    "url",
    "column_name",
    "publisher",
    "summary",
    "content",
    "content_length",
    "match_level",
    "matched_keywords",
    "source_page",
    "raw_path",
    "http_status",
    "crawl_time",
    "batch_time",
    "image_urls",
)

# 主键/来源字段不在更新列表里：同一 URL 重复入库时它们必须保持原值
_UPDATE_COLUMNS: Tuple[str, ...] = tuple(
    c for c in COLUMNS if c not in ("doc_id", "source", "url")
)

# 列名 → 列定义（顺序即 COLUMNS 的顺序）；DDL 与测试都从这里取
COLUMN_DEFS: Mapping[str, str] = {
    "doc_id": "VARCHAR(64) NOT NULL COMMENT 'md5(url)'",
    "source": "VARCHAR(32) NOT NULL COMMENT '源 key'",
    "publish_date": "DATE NULL COMMENT '发布日期'",
    "title": "VARCHAR(512) NULL COMMENT '标题'",
    "url": "VARCHAR(1024) NULL COMMENT '原文链接'",
    "column_name": "VARCHAR(128) NULL COMMENT '栏目/频道'",
    "publisher": "VARCHAR(256) NULL COMMENT '发布方/来源媒体'",
    "summary": "TEXT NULL COMMENT '摘要'",
    "content": "LONGTEXT NULL COMMENT '正文'",
    "content_length": "INT NULL COMMENT '正文字数'",
    "match_level": "VARCHAR(32) NULL COMMENT '命中级别(server/title/body/title+body)'",
    "matched_keywords": "VARCHAR(1024) NULL COMMENT '命中关键词(逗号分隔)'",
    "source_page": "VARCHAR(512) NULL COMMENT '来源页面/检索词页'",
    "raw_path": "VARCHAR(512) NULL COMMENT '原始快照路径'",
    "http_status": "INT NULL COMMENT '详情页 HTTP 状态码'",
    "crawl_time": "DATETIME NULL COMMENT '该条实际入库时刻'",
    "batch_time": "DATETIME NULL COMMENT '批次时间(一轮采集共用)'",
    "image_urls": "JSON NULL COMMENT '图片 URL 数组(限量3张，存外链)'",
}
_COLUMN_DDL: Tuple[str, ...] = tuple(f"`{c}` {COLUMN_DEFS[c]}" for c in COLUMNS)
_PRIMARY_KEY_DDL = "PRIMARY KEY (doc_id)"

# 列清单是模块常量（18 个固定列名）拼出来的，不含任何外部数据
_INSERT_HEAD = "INSERT INTO {table} (" + ", ".join(f"`{c}`" for c in COLUMNS) + ") VALUES "  # nosec B608
_ON_DUP = " ON DUPLICATE KEY UPDATE " + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in _UPDATE_COLUMNS)


def db_name_ok(db_name: str) -> str:
    """库名也拼进 SQL（CREATE DATABASE / 限定表名），只允许标识符字符。"""
    name = "" if db_name is None else str(db_name).strip()
    if not name or not set(name) <= _DB_NAME_OK:
        raise ValueError(f"非法的 db_name：{db_name!r}（只允许小写字母/数字/下划线/美元符）")
    return name


def table_name(source_key: str) -> str:
    """由源 key 得到表名；key 不合法就报错，不猜也不转写。"""
    key = "" if source_key is None else str(source_key).strip()
    if not key or not set(key) <= _SOURCE_KEY_OK:
        raise ValueError(f"非法的 source_key：{source_key!r}（只允许小写字母/数字/下划线，如 people、ccg_wqzf）")
    return f"ods_{key}_news"


def _quote_table(db_name: str, source_key: str) -> str:
    """限定表名。两侧都先过白名单校验，所以这里的拼接不含外部可控文本。"""
    return f"`{db_name_ok(db_name)}`.`{table_name(source_key)}`"


def _table_comment(source_name: str) -> str:
    """表注释用源显示名；转义单引号与反斜杠，避免拼坏 DDL。"""
    name = (source_name or "").replace("\\", "\\\\").replace("'", "''")
    return name


# ---------------------------------------------------------------- 连接


def connect(
    db_name: str = DEFAULT_DB_NAME,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    **kwargs: Any,
) -> Any:
    """连 MySQL 实例。

    刻意**不指定** database：库要由 `ensure_schema` 去建，库还不存在时这里也不能报错。
    `db_name` 参数只为兼容调用方签名而保留。
    """
    try:
        import pymysql
    except ImportError as exc:  # 驱动按 uv 注入，缺席时报清楚怎么补
        raise RuntimeError(
            "缺少 PyMySQL 驱动：请用 uv run --isolated --extra ai --with pymysql python ... 运行"
        ) from exc

    connect_kwargs: Dict[str, Any] = {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "charset": _CHARSET,
        "autocommit": False,
    }
    connect_kwargs.update(kwargs)
    return pymysql.connect(**connect_kwargs)


# ---------------------------------------------------------------- 建库建表


def ensure_database(conn: Any, db_name: str) -> None:
    """建库（若缺）。库名先过白名单，字符集/排序规则来自模块常量，没有外部可拼接的文本。"""
    name = db_name_ok(db_name)
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{name}` "
            f"DEFAULT CHARACTER SET {_CHARSET} COLLATE {_COLLATION}"
        )
    conn.commit()


def create_table_sql(db_name: str, source_key: str, source_name: str) -> str:
    """拼出建表语句；CLI 的 --dry-run 也用它，保证打印的和实跑的是同一条 SQL。

    表名/库名走白名单校验（见 `_quote_table`），列定义与字符集都是模块常量，没有数据值参与拼接。
    """
    body = ",\n  ".join((*_COLUMN_DDL, _PRIMARY_KEY_DDL))
    table = _quote_table(db_name, source_key)  # nosec B608
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        f"  {body}\n"
        f") ENGINE=InnoDB DEFAULT CHARSET={_CHARSET} COLLATE={_COLLATION} "
        f"COMMENT='{_table_comment(source_name)}'"
    )


def ensure_schema(conn: Any, source_key: str, source_name: str, db_name: str = DEFAULT_DB_NAME) -> None:
    """建库（若缺）+ 建该源的表（若缺）。每轮采集调用都幂等。"""
    ensure_database(conn, db_name)
    with conn.cursor() as cur:
        cur.execute(create_table_sql(db_name, source_key, source_name))
    conn.commit()


# ---------------------------------------------------------------- 字段规整


def _as_text(value: Any) -> Optional[str]:
    """文本字段：缺值/空串给 None（入库存 NULL），保留真实的非空文本。

    与 Doris 样本一致：缺失就是 NULL，"" 和 NULL 不混用，否则「没采到」和「采到空」分不清。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (list, tuple, set)):
        return _as_text(",".join(str(v) for v in value if v is not None and str(v) != ""))
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> Optional[int]:
    """int 转换：能转就转，转不了给 None（不抛，采集侧字段会变）。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> Optional[datetime]:
    """宽容解析时间：datetime/date 直用，字符串试几种常见格式，解析不出给 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None

    # 统一分隔符：中文年月日 → 数字，'T' → 空格，压掉重复空白
    normalized = text.replace("年", "-").replace("月", "-").replace("日", " ").replace("T", " ")
    normalized = " ".join(normalized.split())
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _to_date(value: Any) -> Optional[date]:
    """`publish_date` 列是 DATE（对齐 Doris 样本），所以入参先按时间解析再取日期部分。"""
    parsed = _as_datetime(value)
    return None if parsed is None else parsed.date()


def _as_json_array(value: Any, limit: int = 3) -> Optional[str]:
    """图片外链 → JSON 数组字符串（只取前 limit 张）。没有图片就存 NULL。"""
    items: List[str] = []
    if isinstance(value, (list, tuple, set)):
        candidates: Iterable[Any] = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            candidates = ()
        else:
            try:
                loaded = json.loads(text)
            except ValueError:
                candidates = [text]  # 逗号串/单条地址都按单值处理
            else:
                candidates = loaded if isinstance(loaded, list) else [loaded]
    elif value is None:
        candidates = ()
    else:
        candidates = [value]

    for item in candidates:
        url = "" if item is None else str(item).strip()
        if not url:
            continue
        items.append(url)
        if len(items) >= limit:
            break

    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    """按优先级取第一个非空字段：采集侧字段名会变，缺键按空值处理。"""
    for key in keys:
        if key in record:
            value = record[key]
            if value is not None and value != "" and value != []:
                return value
    return None


def doc_id_for(url: str) -> str:
    """doc_id = md5(url)。

    这里用 md5 只是为了得到稳定的定长行标识（对齐 Doris 样本的 `md5(url)`），
    不是安全用途，所以显式声明 usedforsecurity=False。
    """
    return hashlib.md5((url or "").encode("utf-8"), usedforsecurity=False).hexdigest()


def _matches(record: Mapping[str, Any], *keys: str) -> Optional[str]:
    """命中关键词：列表拼成逗号串，字符串原样用。"""
    return _as_text(_first(record, *keys))


def _normalize(record: Mapping[str, Any], source_key: str, source_name: str, batch_dt: Optional[datetime],
               crawl_dt: datetime) -> Optional[Tuple[Any, ...]]:
    """一条采集记录 → 一行参数；url 为空则返回 None（调用方记 skipped）。"""
    url = _as_text(_first(record, "url", "link", "detail_url"))
    if not url:
        return None

    body = _first(record, "body", "content")
    body_text = "" if body is None else str(body)
    matched = _matches(record, "matched_keywords", "keywords", "keyword_hits")

    row = (
        doc_id_for(url),
        _as_text(_first(record, "source")) or source_key,
        _to_date(_first(record, "published", "publish_date", "pub_date", "date")),
        _as_text(_first(record, "title", "list_title")),
        url,
        _as_text(_first(record, "category", "column_name", "channel")),
        _as_text(_first(record, "publisher", "source_media", "author")),
        _as_text(_first(record, "summary", "abstract")),
        body_text,
        _as_int(_first(record, "body_length", "content_length")) or (len(body_text) or None),
        _as_text(_first(record, "match_level", "level")),
        matched,
        _as_text(_first(record, "source_page", "search_page", "page_url")),
        _as_text(_first(record, "raw_path", "snapshot_path")),
        _as_int(_first(record, "http_status", "status")),
        crawl_dt,
        batch_dt,
        _as_json_array(_first(record, "image_urls", "images")),
    )
    return row


def _utc_batch_time(batch_time: Any) -> Optional[datetime]:
    """batch_time 由 crawl.py 以字符串传入（isoformat，秒级）；也接受 datetime。"""
    if batch_time is None or batch_time == "":
        return None
    parsed = _as_datetime(batch_time)
    if parsed is None:
        # 形式不认识就当没给：宁可 batch_time 为 NULL，也不要整批插入失败
        return None
    return parsed


# ---------------------------------------------------------------- 写库


def upsert_sql(db_name: str, source_key: str) -> str:
    """拼出幂等 upsert 语句（executemany 用）。

    MySQL 不允许把库名/表名做成占位符，所以表名只能拼进 SQL；它来自
    `table_name()` / `db_name_ok()` 的白名单校验（仅 `[a-z0-9_$]`），
    真正的数据值全部走 `%s` 占位符。
    """
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    table = _quote_table(db_name, source_key)  # nosec B608
    return _INSERT_HEAD.format(table=table) + f"({placeholders})" + _ON_DUP


def upsert_records(
    conn: Any,
    source_key: str,
    source_name: str,
    records: Sequence[Mapping[str, Any]],
    batch_time: Any = None,
    db_name: str = DEFAULT_DB_NAME,
) -> Dict[str, Any]:
    """按 doc_id 幂等写入一批记录。

    返回 ``{"inserted": n, "updated": n, "skipped": n, "errors": [...]}``：
    - ``inserted`` / ``updated`` 由 MySQL 的 affected_rows 区分（新插 1、更新 2）；
    - ``skipped`` 是 url 为空的记录（无法算 doc_id）；
    - ``errors`` 是逐条的异常文本，单条失败不拖垮整批。
    """
    result: Dict[str, Any] = {"inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    table = _quote_table(db_name, source_key)
    batch_dt = _utc_batch_time(batch_time)
    crawl_dt = datetime.now()
    sql = upsert_sql(db_name, source_key)

    rows: List[Tuple[Any, ...]] = []
    for index, record in enumerate(records or []):
        if not isinstance(record, Mapping):
            result["errors"].append(f"第 {index} 条不是 dict（{type(record).__name__}），已跳过")
            result["skipped"] += 1
            continue
        try:
            row = _normalize(record, source_key, source_name, batch_dt, crawl_dt)
        except Exception as exc:  # noqa: BLE001 - 单条字段异常要记账并继续
            result["errors"].append(f"第 {index} 条字段规整失败 {type(exc).__name__}: {exc}")
            result["skipped"] += 1
            continue
        if row is None:
            result["skipped"] += 1
            result["errors"].append(f"第 {index} 条缺 url，无法计算 doc_id，已跳过")
            continue
        rows.append(row)

    if not rows:
        return result

    try:
        with conn.cursor() as cur:
            affected = cur.executemany(sql, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # affected_rows 语义：新增 1、命中主键被更新 2；驱动/服务端拿不到时按全新增记
    updated = 0
    inserted = 0
    if isinstance(affected, int) and affected >= 0:
        updated = min(len(rows), max(0, affected - len(rows)))
        inserted = len(rows) - updated
    else:
        inserted = len(rows)
    result["inserted"] = inserted
    result["updated"] = updated
    result["table"] = table
    return result


# ---------------------------------------------------------------- 回灌 JSON


def _read_json_records(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读 JSON：既认 crawl.py 的 {"meta":..., "articles":[...]}，也认裸列表。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)], {}
    if isinstance(payload, dict):
        for key in ("articles", "records", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                meta = payload.get("meta")
                return [r for r in value if isinstance(r, dict)], (meta if isinstance(meta, dict) else {})
        raise ValueError(f"{path}: JSON 里找不到记录数组（认 articles/records/data/items 键，或裸列表）")
    raise ValueError(f"{path}: 顶层既不是列表也不是对象（{type(payload).__name__}）")


def load_records_from_json(conn: Any, path: str, batch_time: Any = None,
                           db_name: str = DEFAULT_DB_NAME, source_key: str = "",
                           source_name: str = "") -> Dict[str, Any]:
    """把 JSON 里的记录回灌入库（crawl.py 的 --from-json 路径）。

    签名按集成契约：``(conn, path, batch_time=...)``。source_key/source_name 缺省从记录里推。
    """
    records, meta = _read_json_records(path)
    key = (source_key or "").strip()
    if not key:
        for record in records:
            candidate = _as_text(_first(record, "source"))
            if candidate:
                key = candidate
                break
    if not key:
        meta_sources = meta.get("sources") if isinstance(meta, dict) else None
        if isinstance(meta_sources, list) and meta_sources:
            key = _as_text(meta_sources[0])
    if not key:
        raise ValueError(f"{path}: 推不出 source_key（记录里没有 source 字段），请显式指定 --source-key")
    table_name(key)  # 早失败：key 不合法就别等到拼 SQL

    name = (source_name or "").strip()
    if not name:
        for record in records:
            candidate = _as_text(_first(record, "source_name"))
            if candidate:
                name = candidate
                break
    name = name or key

    if batch_time is None or batch_time == "":
        batch_time = meta.get("batch_time") if isinstance(meta, dict) else None
    if batch_time is None or batch_time == "":
        batch_time = datetime.now().isoformat(timespec="seconds")

    ensure_schema(conn, key, name, db_name=db_name)
    return upsert_records(conn, key, name, records, batch_time, db_name=db_name)


# ---------------------------------------------------------------- CLI


def _resolve_key_name(args: argparse.Namespace, records: Sequence[Mapping[str, Any]]) -> Tuple[str, str]:
    key = (args.source_key or "").strip()
    name = (args.source_name or "").strip()
    if not key:
        for record in records:
            key = _as_text(_first(record, "source"))
            if key:
                break
    if not name:
        for record in records:
            name = _as_text(_first(record, "source_name"))
            if name:
                break
    if not key:
        raise SystemExit("❌ 推不出 source_key：JSON 里没有 source 字段，请加 --source-key")
    return key, (name or key)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 gov_news 采集记录写进 MySQL 8.0（库 newscrawl，每源一张表 ods_<key>_news，"
                    "doc_id=md5(url) 幂等 upsert）")
    parser.add_argument("--from-json", default="", help="要回灌的 JSON（crawl.py --dump-records 的产物，或裸记录列表）")
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME, help=f"MySQL 库名（默认 {DEFAULT_DB_NAME}）")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"MySQL 主机（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"MySQL 端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"MySQL 用户（默认 {DEFAULT_USER}）")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="MySQL 密码（默认取内置默认值；日志里不回显）")
    parser.add_argument("--source-key", default="", help="表名用的源 key（缺省从 JSON 记录里推）")
    parser.add_argument("--source-name", default="", help="表注释用的源显示名（缺省从 JSON 记录里推）")
    parser.add_argument("--batch-time", default="", help="批次时间（缺省用 JSON 的 meta.batch_time，再缺省用当前时刻）")
    parser.add_argument("--dry-run", action="store_true", help="只打印要执行的 DDL/DML，不连库不改数据")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.from_json:
        print("❌ 本 CLI 只做回灌：请用 --from-json <文件>（采集入库由 crawl.py 调用本模块完成）")
        return 2
    path = Path(args.from_json)
    if not path.exists():
        print(f"❌ 找不到 JSON 文件：{path}")
        return 2

    records, meta = _read_json_records(str(path))
    try:
        key, name = _resolve_key_name(args, records)
    except SystemExit as exc:
        print(exc)
        return 2

    if args.dry_run:
        batch_time = args.batch_time or (meta.get("batch_time") if isinstance(meta, dict) else "") or ""
        batch_time = batch_time or datetime.now().isoformat(timespec="seconds")
        print(f"-- 库 {args.db_name} 源 {key}({name}) 记录 {len(records)} 条 batch_time={batch_time}")
        print(f"CREATE DATABASE IF NOT EXISTS `{args.db_name}` "
              f"DEFAULT CHARACTER SET {_CHARSET} COLLATE {_COLLATION};")
        print(create_table_sql(args.db_name, key, name) + ";")
        print(upsert_sql(args.db_name, key) + ";")
        sample = records[0] if records else {}
        if sample:
            row = _normalize(sample, key, name, _utc_batch_time(batch_time), datetime.now())
            if row is not None:
                shown = ["<content省略>" if c == "content" else v for c, v in zip(COLUMNS, row)]
                print("\n-- 第 1 条参数（content 已省略）：")
                for column, value in zip(COLUMNS, shown):
                    print(f"--   {column} = {value!r}")
        print("\n-- dry-run：未连库、未建表、未写数据")
        return 0

    try:
        conn = connect(db_name=args.db_name, host=args.host, port=args.port,
                       user=args.user, password=args.password)
    except Exception as exc:  # noqa: BLE001 - CLI 要把失败原因原样报出来
        print(f"❌ 连接数据库失败 {type(exc).__name__}: {exc}")
        return 2

    batch_time = args.batch_time or (meta.get("batch_time") if isinstance(meta, dict) else "") or ""
    batch_time = batch_time or datetime.now().isoformat(timespec="seconds")
    try:
        result = load_records_from_json(conn, str(path), batch_time, db_name=args.db_name,
                                        source_key=key, source_name=name)
    except Exception as exc:  # noqa: BLE001 - 同上
        print(f"❌ 回灌失败 {type(exc).__name__}: {exc}")
        return 2
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响已提交的结果
            pass

    print(f"回灌 {path} -> {result.get('table')} 源={key}({name}) batch_time={batch_time}")
    print(f"结果：inserted={result['inserted']} updated={result['updated']} "
          f"skipped={result['skipped']} errors={len(result['errors'])}")
    for err in result["errors"][:5]:
        print(f"  ! {err}")
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
