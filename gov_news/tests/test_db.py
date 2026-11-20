"""`gov_news/db.py` 的测试。

两条腿：
- **纯函数**（表名/库名校验、DDL 拼装、日期与 JSON 规整）不依赖数据库；
- **实库语义**（建表结构、幂等 upsert、batch_time 更新、JSON 截断）跑真实 MySQL，
  不 mock SQL —— 幂等和类型是 MySQL 的行为，mock 掉就什么都没验。

跑法（仓库根目录）：
    uv run --isolated --extra ai --with pymysql --with pytest python -m pytest gov_news/tests -p no:cacheprovider

MySQL 连不上时实库用例整体 skip，纯函数用例照常跑。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

GOV_NEWS = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (str(GOV_NEWS), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import db  # noqa: E402
import dbcheck  # noqa: E402

from dbcheck import TEST_KEY, TEST_NAME  # noqa: E402


def make_record(**overrides):
    record = {
        "source": TEST_KEY,
        "source_name": TEST_NAME,
        "url": "https://example.gov.cn/a/1.html",
        "title": "第一条：南海航行自由的官方表态",
        "body": "正文甲" * 3,
        "body_length": 9,
        "published": "2026-01-30 15:04:05",
        "summary": "摘要一",
        "category": "海洋权益",
        "publisher": "新华社",
        "matched_keywords": ["南海", "航行自由"],
        "match_level": "server",
        "source_page": "检索页p1",
        "http_status": 200,
        "image_urls": ["https://img/a.jpg", "https://img/b.jpg", "https://img/c.jpg", "https://img/d.jpg"],
        "search_keyword": "南海",
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------- 表名与库名


def test_table_name_ok():
    assert db.table_name("people") == "ods_people_news"
    assert db.table_name("ccg_wqzf") == "ods_ccg_wqzf_news"
    assert db.table_name("  people  ") == "ods_people_news"


@pytest.mark.parametrize("bad", [
    "People",      # 大写
    "a-b",         # 连字符
    "",            # 空
    "   ",         # 全空白
    "中文",         # 非 ASCII
    "a b",         # 空格
    "tbl;DROP",    # 注入形态
    "a';--",
    "ods.",        # 点号
    "a`b",
    None,
])
def test_table_name_rejects_illegal_key(bad):
    with pytest.raises(ValueError):
        db.table_name(bad)


def test_db_name_ok_and_rejects_illegal():
    assert db.db_name_ok("newscrawl") == "newscrawl"
    assert db.db_name_ok("  newscrawl  ") == "newscrawl"
    for bad in ("new-scrawl", "", "  ", "new scrawl", "new;DROP", "库", None, "new.`x`"):
        with pytest.raises(ValueError):
            db.db_name_ok(bad)


# ---------------------------------------------------------------- DDL 拼装


def test_create_table_sql_shape():
    sql = db.create_table_sql("newscrawl", "people", "人民网")
    assert sql.startswith("CREATE TABLE IF NOT EXISTS `newscrawl`.`ods_people_news` (")
    assert "ENGINE=InnoDB" in sql
    assert "DEFAULT CHARSET=utf8mb4" in sql
    assert "COLLATE=utf8mb4_general_ci" in sql
    assert sql.rstrip().endswith("COMMENT='人民网'")
    assert "  PRIMARY KEY (doc_id)\n" in sql

    # 字段顺序 = 契约里的 18 列（逐行比对，不靠 index() 撞注释里的同名文字）
    body_lines = [line.strip().rstrip(",") for line in sql.splitlines()[1:-1]]
    assert body_lines == [f"`{c}` {db.COLUMN_DEFS[c]}" for c in db.COLUMNS] + ["PRIMARY KEY (doc_id)"]
    assert list(db.COLUMN_DEFS) == list(db.COLUMNS), "列定义表必须与 COLUMNS 同序同集"
    assert sql.count("`content` LONGTEXT NULL") == 1
    assert sql.count("`image_urls` JSON NULL") == 1


def test_create_table_sql_escapes_comment():
    sql = db.create_table_sql("newscrawl", "people", "O'Brien\\x")
    assert "COMMENT='O''Brien\\\\x'" in sql


def test_upsert_sql_updates_business_fields_only():
    sql = db.upsert_sql("newscrawl", "people")
    assert sql.startswith("INSERT INTO `newscrawl`.`ods_people_news` (`doc_id`, `source`,")
    assert "ON DUPLICATE KEY UPDATE" in sql
    for column in ("`doc_id`=VALUES", "`source`=VALUES", "`url`=VALUES"):
        assert column not in sql
    for column in ("`title`=VALUES(`title`)", "`batch_time`=VALUES(`batch_time`)",
                   "`image_urls`=VALUES(`image_urls`)", "`crawl_time`=VALUES(`crawl_time`)"):
        assert column in sql
    assert len(db.COLUMNS) == 18 and db.COLUMNS[0] == "doc_id" and db.COLUMNS[-1] == "image_urls"


# ---------------------------------------------------------------- 日期 / JSON / doc_id


@pytest.mark.parametrize("raw,expected", [
    ("2026-01-30 15:04:05", datetime(2026, 1, 30, 15, 4, 5)),
    ("2026-01-30T15:04:05", datetime(2026, 1, 30, 15, 4, 5)),
    ("2026年1月30日", datetime(2026, 1, 30)),
    ("2026年1月30日 15:04", datetime(2026, 1, 30, 15, 4)),
    ("2026-01-30", datetime(2026, 1, 30)),
    ("2026/01/30", datetime(2026, 1, 30)),
    ("2026.01.30", datetime(2026, 1, 30)),
    ("20260130", datetime(2026, 1, 30)),
    ("  2026-01-30  ", datetime(2026, 1, 30)),
])
def test_as_datetime_accepts_common_formats(raw, expected):
    assert db._as_datetime(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "昨天", "2026-13-99", "2026", 12345])
def test_as_datetime_returns_none_without_raising(raw):
    assert db._as_datetime(raw) is None


def test_as_int_tolerates_junk():
    assert db._as_int("200") == 200
    assert db._as_int(200) == 200
    assert db._as_int("") is None
    assert db._as_int("N/A") is None
    assert db._as_int(None) is None


def test_as_json_array_limits_to_three():
    assert json.loads(db._as_json_array(["a", "b", "c", "d", "e"])) == ["a", "b", "c"]
    assert db._as_json_array([]) is None
    assert db._as_json_array(None) is None
    assert db._as_json_array("") is None
    assert json.loads(db._as_json_array("https://img/a.jpg")) == ["https://img/a.jpg"]
    assert json.loads(db._as_json_array('["https://img/a.jpg","https://img/b.jpg"]')) == \
        ["https://img/a.jpg", "https://img/b.jpg"]


def test_doc_id_is_md5_of_url():
    url = "https://example.gov.cn/a/1.html"
    expected = hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest()
    assert db.doc_id_for(url) == expected
    assert len(db.doc_id_for(url)) == 32, "md5 十六进制摘要 32 字符（列是 varchar(64)，留了余量）"


def test_normalize_handles_missing_keys():
    """采集侧字段会变：只有 url 也必须能落库，不抛 KeyError。"""
    row = db._normalize({"url": "https://example.gov.cn/x.html"}, TEST_KEY, TEST_NAME, None, datetime.now())
    assert row is not None
    values = dict(zip(db.COLUMNS, row))
    assert values["url"] == "https://example.gov.cn/x.html"
    assert values["source"] == TEST_KEY          # 缺 source 时退回调用方的 key
    assert values["publish_date"] is None
    assert values["image_urls"] is None
    assert values["content"] == ""


def test_normalize_without_url_is_skipped():
    assert db._normalize({"title": "没有 url"}, TEST_KEY, TEST_NAME, None, datetime.now()) is None


def test_batch_time_string_is_parsed():
    """crawl.py 传进来的 batch_time 是 isoformat 字符串，必须解析成 datetime（或 NULL）。"""
    assert db._utc_batch_time("2026-11-20T13:02:14") == datetime(2026, 11, 20, 13, 2, 14)
    assert db._utc_batch_time(datetime(2026, 11, 20, 13, 2, 14)) == datetime(2026, 11, 20, 13, 2, 14)
    assert db._utc_batch_time("") is None
    assert db._utc_batch_time(None) is None
    assert db._utc_batch_time("不是时间") is None


# ---------------------------------------------------------------- 实库：建表结构


def test_ensure_schema_creates_table_and_is_idempotent(conn, fresh_table):
    db.ensure_schema(conn, TEST_KEY, TEST_NAME)   # 再来两次也不报错
    db.ensure_schema(conn, TEST_KEY, TEST_NAME)
    got = dbcheck.info_query(conn, "SELECT COUNT(*) AS n FROM information_schema.tables "
                                   "WHERE table_schema='newscrawl' AND table_name=%s", (fresh_table,))
    assert got[0]["n"] == 1


def test_created_table_matches_contract(conn, fresh_table):
    # MySQL 8 的 data dictionary 把 information_schema 的列名回成大写
    got = dbcheck.info_query(
        conn,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT "
        "FROM information_schema.columns WHERE table_schema='newscrawl' AND table_name=%s "
        "ORDER BY ORDINAL_POSITION", (fresh_table,))
    assert [row["COLUMN_NAME"] for row in got] == list(db.COLUMNS), "字段名与顺序必须与 Doris 样本一致"
    by_name = {row["COLUMN_NAME"]: row for row in got}

    assert by_name["doc_id"]["COLUMN_TYPE"] == "varchar(64)" and by_name["doc_id"]["IS_NULLABLE"] == "NO"
    assert by_name["doc_id"]["COLUMN_KEY"] == "PRI" and by_name["doc_id"]["COLUMN_COMMENT"] == "md5(url)"
    assert by_name["content"]["COLUMN_TYPE"] == "longtext"
    assert by_name["image_urls"]["COLUMN_TYPE"] == "json"
    assert by_name["image_urls"]["COLUMN_COMMENT"] == "图片 URL 数组(限量3张，存外链)"
    assert by_name["publish_date"]["COLUMN_TYPE"] == "date"          # 对齐 Doris 样本的 date
    assert by_name["batch_time"]["COLUMN_TYPE"] == "datetime"
    assert by_name["crawl_time"]["COLUMN_TYPE"] == "datetime"
    assert by_name["source"]["COLUMN_TYPE"] == "varchar(32)"         # 对齐样本
    assert by_name["publisher"]["COLUMN_TYPE"] == "varchar(256)"
    assert by_name["summary"]["COLUMN_TYPE"] == "text"
    assert by_name["matched_keywords"]["COLUMN_TYPE"] == "varchar(1024)"
    assert by_name["source_page"]["COLUMN_TYPE"] == "varchar(512)"
    assert by_name["raw_path"]["COLUMN_TYPE"] == "varchar(512)"
    assert by_name["match_level"]["COLUMN_TYPE"] == "varchar(32)"    # 比样本(16)宽，已确认保留
    assert by_name["url"]["COLUMN_TYPE"] == "varchar(1024)"          # 比样本(512)宽，已确认保留
    assert by_name["source"]["IS_NULLABLE"] == "NO" and by_name["source"]["COLUMN_COMMENT"] == "源 key"

    table = dbcheck.info_query(
        conn,
        "SELECT ENGINE AS engine, TABLE_COLLATION AS collation, TABLE_COMMENT AS comment "
        "FROM information_schema.tables WHERE table_schema='newscrawl' AND table_name=%s", (fresh_table,))[0]
    assert table["engine"] == "InnoDB"
    assert table["collation"] == "utf8mb4_general_ci"
    assert table["comment"] == TEST_NAME

    create_sql = dbcheck.info_query(conn, f"SHOW CREATE TABLE {dbcheck.qtable(TEST_KEY)}")[0]["Create Table"]
    assert "PRIMARY KEY (`doc_id`)" in create_sql
    assert "DEFAULT CHARSET=utf8mb4" in create_sql
    assert "ENGINE=InnoDB" in create_sql
    assert "`image_urls` json" in create_sql
    assert "`content` longtext" in create_sql


# ---------------------------------------------------------------- 实库：幂等 upsert


def test_first_insert_counts(conn, fresh_table):
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record()], "2026-11-20T13:02:14")
    assert result["inserted"] == 1 and result["updated"] == 0
    assert result["skipped"] == 0 and result["errors"] == []
    assert dbcheck.count_rows(conn, TEST_KEY) == 1

    row = dbcheck.rows_from(
        conn, TEST_KEY,
        "doc_id, source, url, title, publish_date, image_urls, batch_time, crawl_time")[0]
    assert row["doc_id"] == hashlib.md5(b"https://example.gov.cn/a/1.html", usedforsecurity=False).hexdigest()
    assert row["source"] == TEST_KEY and row["url"] == "https://example.gov.cn/a/1.html"
    assert row["title"] == "第一条：南海航行自由的官方表态"
    assert row["publish_date"] == date(2026, 1, 30)   # DATE 列只保留到天
    assert json.loads(row["image_urls"]) == ["https://img/a.jpg", "https://img/b.jpg", "https://img/c.jpg"]
    assert row["batch_time"] == datetime(2026, 11, 20, 13, 2, 14)   # 字符串 batch_time 已解析入库
    assert isinstance(row["crawl_time"], datetime)                  # crawl_time 是真的写库时刻


def test_duplicate_url_in_same_batch_keeps_last_value(conn, fresh_table):
    records = [
        make_record(title="旧标题", summary="旧摘要", image_urls=["https://img/old.jpg"]),
        make_record(title="新标题", summary="新摘要", image_urls=["https://img/new.jpg"]),
    ]
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, records, "2026-11-20T13:02:14")
    assert result["inserted"] == 1 and result["updated"] == 1
    assert dbcheck.count_rows(conn, TEST_KEY) == 1, "同 URL 只能有一行"

    row = dbcheck.rows_from(conn, TEST_KEY, "title, summary, image_urls")[0]
    assert row["title"] == "新标题" and row["summary"] == "新摘要"
    assert json.loads(row["image_urls"]) == ["https://img/new.jpg"]


def test_second_batch_updates_and_does_not_grow(conn, fresh_table):
    db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record()], "2026-11-20T13:02:14")
    before = dbcheck.rows_from(conn, TEST_KEY, "doc_id, url, batch_time")[0]

    result = db.upsert_records(conn, TEST_KEY, TEST_NAME,
                               [make_record(title="改过的标题", matched_keywords=["南海"])],
                               "2026-11-21T09:00:00")
    assert result["inserted"] == 0 and result["updated"] == 1
    assert dbcheck.count_rows(conn, TEST_KEY) == 1

    after = dbcheck.rows_from(conn, TEST_KEY, "doc_id, url, batch_time, title, matched_keywords")[0]
    assert after["doc_id"] == before["doc_id"], "doc_id 不能被更新"
    assert after["url"] == before["url"], "url 不能被更新"
    assert after["batch_time"] == datetime(2026, 11, 21, 9, 0, 0), "batch_time 必须被更新"
    assert after["title"] == "改过的标题"
    assert after["matched_keywords"] == "南海", "list 形态的 matched_keywords 要落成逗号串"


def test_matched_keywords_list_becomes_comma_string(conn, fresh_table):
    db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record()], "2026-11-20T13:02:14")
    assert dbcheck.rows_from(conn, TEST_KEY, "matched_keywords")[0]["matched_keywords"] == "南海,航行自由"


def test_missing_url_record_is_skipped_not_fatal(conn, fresh_table):
    records = [{"title": "没有 url"}, make_record()]
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, records, "2026-11-20T13:02:14")
    assert result["skipped"] == 1
    assert result["inserted"] == 1
    assert len(result["errors"]) == 1 and "url" in result["errors"][0]
    assert dbcheck.count_rows(conn, TEST_KEY) == 1


def test_bad_element_type_is_skipped_not_fatal(conn, fresh_table):
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, ["这不是 dict", make_record()], "2026-11-20T13:02:14")
    assert result["skipped"] == 1 and result["inserted"] == 1


def test_empty_batch_returns_zeroes_without_touching_db(conn, fresh_table):
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, [], "2026-11-20T13:02:14")
    assert result == {"inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    assert dbcheck.count_rows(conn, TEST_KEY) == 0


def test_unchanged_rerun_does_not_grow_table(conn, fresh_table):
    """同一批内容重跑：MySQL 的 affected_rows 可能为 0（值没变），但行数绝不能涨。"""
    db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record()], "2026-11-20T13:02:14")
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record()], "2026-11-20T13:03:00")
    assert result["inserted"] == 0
    assert result["inserted"] + result["updated"] == 1
    assert dbcheck.count_rows(conn, TEST_KEY) == 1
    assert dbcheck.rows_from(conn, TEST_KEY, "batch_time")[0]["batch_time"] == datetime(2026, 11, 20, 13, 3, 0)


def test_unparseable_published_becomes_null(conn, fresh_table):
    db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record(published="去年今天")], "2026-11-20T13:02:14")
    assert dbcheck.rows_from(conn, TEST_KEY, "publish_date")[0]["publish_date"] is None


def test_chinese_date_and_missing_keys_land_in_db(conn, fresh_table):
    record = {"url": "https://example.gov.cn/b/2.html", "title": "第二条", "published": "2026年1月30日"}
    db.upsert_records(conn, TEST_KEY, TEST_NAME, [record], "2026-11-20T13:02:14")
    row = dbcheck.rows_from(conn, TEST_KEY,
                            "source, publish_date, summary, image_urls, matched_keywords, http_status")[0]
    assert row["source"] == TEST_KEY
    assert row["publish_date"] == date(2026, 1, 30)
    assert row["summary"] is None and row["image_urls"] is None
    assert row["matched_keywords"] is None and row["http_status"] is None


def test_missing_batch_time_writes_null_and_keeps_row(conn, fresh_table):
    """batch_time 缺失/不可解析时也不能让插入失败。"""
    result = db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record()], None)
    assert result["inserted"] == 1 and result["errors"] == []
    assert dbcheck.rows_from(conn, TEST_KEY, "batch_time")[0]["batch_time"] is None


def test_long_content_survives_roundtrip(conn, fresh_table):
    """content 是 LONGTEXT：远大于 varchar 上限的正文要能原样回读。"""
    body = "南海" * 60000        # 12 万个字符
    db.upsert_records(conn, TEST_KEY, TEST_NAME, [make_record(body=body, body_length=len(body))],
                      "2026-11-20T13:02:14")
    got = dbcheck.info_query(conn, f"SELECT CHAR_LENGTH(content) AS n FROM {dbcheck.qtable(TEST_KEY)}")  # nosec B608
    assert got[0]["n"] == len(body)


def test_illegal_source_key_rejected_before_sql(conn):
    with pytest.raises(ValueError):
        db.upsert_records(conn, "Bad-Key", TEST_NAME, [make_record()], "2026-11-20T13:02:14")
    with pytest.raises(ValueError):
        db.ensure_schema(conn, "Bad-Key", TEST_NAME)


# ---------------------------------------------------------------- 回灌 JSON


def test_load_records_from_json_roundtrip(conn, fresh_table, tmp_path):
    dump = tmp_path / "people.json"
    dump.write_text(json.dumps({
        "meta": {"batch_time": "2026-11-20T13:02:14", "sources": [TEST_KEY]},
        "articles": [make_record(), make_record(url="https://example.gov.cn/a/2.html", title="第二条")],
    }, ensure_ascii=False), encoding="utf-8")

    result = db.load_records_from_json(conn, str(dump), batch_time="2026-11-20T13:02:14")
    assert result["inserted"] == 2 and result["errors"] == []
    assert dbcheck.count_rows(conn, TEST_KEY) == 2
    assert dbcheck.rows_from(conn, TEST_KEY, "batch_time")[0]["batch_time"] == datetime(2026, 11, 20, 13, 2, 14)


def test_load_records_from_json_accepts_bare_list(conn, fresh_table, tmp_path):
    dump = tmp_path / "bare.json"
    dump.write_text(json.dumps([make_record()], ensure_ascii=False), encoding="utf-8")
    result = db.load_records_from_json(conn, str(dump), batch_time="2026-11-20T13:02:14")
    assert result["inserted"] == 1


def test_load_records_from_json_rejects_unusable_payload(conn, fresh_table, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"meta": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        db.load_records_from_json(conn, str(bad), batch_time="2026-11-20T13:02:14")


def test_load_records_from_json_needs_source_key(conn, tmp_path):
    dump = tmp_path / "nokey.json"
    dump.write_text(json.dumps([{"url": "https://example.gov.cn/z.html"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        db.load_records_from_json(conn, str(dump), batch_time="2026-11-20T13:02:14")


def test_crawl_from_json_restores_wiped_fields(conn, fresh_table, tmp_path):
    """真的走 crawl.py 的 --from-json 分支（它按契约调 db.load_records_from_json）。

    回灌语义：同 URL 的记录要把业务字段写回去，行数不涨。
    注意 MySQL 默认 REPEATABLE READ —— 读之前必须 commit，否则读到的是旧快照。
    """
    scramble = tmp_path / "scramble.json"
    scramble.write_text(json.dumps({
        "meta": {"batch_time": "2026-11-20T13:02:14", "sources": [TEST_KEY]},
        "articles": [make_record(url="https://example.gov.cn/fromjson.html", title="原始标题")],
    }, ensure_ascii=False), encoding="utf-8")
    crawl = GOV_NEWS / "crawl.py"
    probe = subprocess.run([sys.executable, "-c", "import scrapling"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("当前解释器没有 scrapling，跳过 crawl.py 子进程联调")

    first = subprocess.run([sys.executable, str(crawl), "--from-json", str(scramble)],
                           capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "inserted" in first.stdout

    conn.commit()   # 结束读事务，拿到最新快照
    assert dbcheck.rows_from(conn, TEST_KEY, "title")[0]["title"] == "原始标题"

    dbcheck.execute(conn, f"UPDATE {dbcheck.qtable(TEST_KEY)} SET title='已抹掉', summary=NULL, "  # nosec B608
                          "matched_keywords=NULL, image_urls=NULL")

    second = subprocess.run([sys.executable, str(crawl), "--from-json", str(scramble)],
                            capture_output=True, text=True, encoding="utf-8")
    assert second.returncode == 0, second.stdout + second.stderr
    conn.commit()

    row = dbcheck.rows_from(conn, TEST_KEY, "title, matched_keywords, image_urls")[0]
    assert row["title"] == "原始标题", "回灌必须把被抹掉的业务字段写回去"
    assert row["matched_keywords"] == "南海,航行自由"
    assert json.loads(row["image_urls"]) == ["https://img/a.jpg", "https://img/b.jpg", "https://img/c.jpg"]
    assert dbcheck.count_rows(conn, TEST_KEY) == 1, "回灌不能让行数增长"


# ---------------------------------------------------------------- CLI


def test_cli_requires_from_json():
    assert db.main([]) == 2


def test_cli_missing_file(tmp_path, capsys):
    assert db.main(["--from-json", str(tmp_path / "nope.json")]) == 2
    assert "找不到 JSON 文件" in capsys.readouterr().out


def test_cli_dry_run_prints_sql_without_touching_db(tmp_path, capsys):
    dump = tmp_path / "p.json"
    dump.write_text(json.dumps({"articles": [make_record()]}, ensure_ascii=False), encoding="utf-8")
    assert db.main(["--from-json", str(dump), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "CREATE DATABASE IF NOT EXISTS `newscrawl`" in out
    assert "CREATE TABLE IF NOT EXISTS `newscrawl`.`ods_db_selftest_news`" in out
    assert "ON DUPLICATE KEY UPDATE" in out
    assert "dry-run：未连库、未建表、未写数据" in out


def test_cli_help_lists_contract_options(capsys):
    with pytest.raises(SystemExit) as exc:
        db.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for option in ("--from-json", "--db-name", "--dry-run", "--source-key", "--source-name"):
        assert option in out


def test_cli_never_echoes_password(tmp_path, capsys):
    """密码不能出现在 CLI 输出里（日志/报告同样不许回显）。"""
    dump = tmp_path / "p.json"
    dump.write_text(json.dumps({"articles": [make_record()]}), encoding="utf-8")
    db.main(["--from-json", str(dump), "--dry-run", "--password", "s3cr3t-not-to-print"])
    help_text = db.build_parser().format_help()
    assert "s3cr3t-not-to-print" not in capsys.readouterr().out
    assert "s3cr3t-not-to-print" not in help_text
