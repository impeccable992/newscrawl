"""实跑验收脚本（不进 pytest 收集：文件名不以 test_ 开头，用 __main__ 手动跑）。

    uv run --isolated --extra ai --with pymysql python gov_news/tests/_manual_accept.py

做三件事：3 条假记录（含 2 条同 URL）入库 → 查行数/字段 → 再跑同一批查幂等与 batch_time 更新。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

GOV_NEWS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOV_NEWS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import dbcheck  # noqa: E402

KEY = "db_selftest"
NAME = "自测源(假数据)"
TABLE = db.table_name(KEY)

RECORDS = [
    {
        "source": KEY,
        "source_name": NAME,
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
    },
    {
        "source": KEY,
        "source_name": NAME,
        "url": "https://example.gov.cn/b/2.html",
        "title": "第二条：中文日期与缺键",
        "body": "正文乙" * 2,
        "body_length": 6,
        # published 用中文日期；其余键故意缺失，验证不抛 KeyError
        "published": "2026年1月30日",
    },
    {
        "source": KEY,
        "source_name": NAME,
        "url": "https://example.gov.cn/a/1.html",  # 与第 1 条同 URL → 应更新而非新增
        "title": "第一条(第二次覆盖)：标题已改写",
        "body": "正文甲-改写版" * 2,
        "body_length": 14,
        "published": "",  # 空串 → NULL，不抛异常
        "summary": "摘要一-改写",
        "category": "海洋权益",
        "matched_keywords": ["南海"],
        "match_level": "title",
        "http_status": 200,
        "image_urls": ["https://img/a.jpg"],
    },
]

def next_second(previous: str) -> str:
    """秒级 isoformat：跨过整秒才能证明 batch_time 真的被更新。"""
    stamp = datetime.now().isoformat(timespec="seconds")
    if stamp == previous:
        time.sleep(1.1)
        stamp = datetime.now().isoformat(timespec="seconds")
    return stamp


def main() -> int:
    conn = db.connect(db_name="newscrawl")
    try:
        dbcheck.drop_table(conn, KEY)          # 从干净状态开始，run1 才是真实的首次插入读数
        db.ensure_schema(conn, KEY, NAME)

        batch_1 = next_second("")
        print(f"[run1] batch_time={batch_1}")
        print(f"[run1] result={db.upsert_records(conn, KEY, NAME, RECORDS, batch_1)}")
        print(f"[run1] row_count={dbcheck.count_rows(conn, KEY)}")
        for row in dbcheck.select(conn, KEY, "ORDER BY title"):
            print(f"[run1] row={dict(row)}")

        batch_2 = next_second(batch_1)
        print(f"[run2] batch_time={batch_2}")
        print(f"[run2] result={db.upsert_records(conn, KEY, NAME, RECORDS, batch_2)}")
        print(f"[run2] row_count={dbcheck.count_rows(conn, KEY)}")
        for row in dbcheck.select(conn, KEY, "ORDER BY title"):
            print(f"[run2] title/batch/crawl={row['title']!r} {row['batch_time']} {row['crawl_time']} "
                  f"imgs={row['image_urls']}")

        # 第三轮：内容一字不改地重跑，验证 MySQL「值未变则 affected_rows=0」的计数语义
        batch_3 = next_second(batch_2)
        print(f"[run3] 内容不变 batch_time={batch_3}")
        print(f"[run3] result={db.upsert_records(conn, KEY, NAME, [dict(RECORDS[0])], batch_3)}")
        print(f"[run3] 只改 title result="
              f"{db.upsert_records(conn, KEY, NAME, [dict(RECORDS[0], title='第一条(第三次改写)')], batch_3)}")

        target = "https://example.gov.cn/a/1.html"
        got = dbcheck.rows_from(conn, KEY, "doc_id", where="url=%s", params=(target,))[0]["doc_id"]
        print(f"[check] doc_id={got} match={got == db.doc_id_for(target)}")

        # 缺 url / 非 dict 的记录要被记 skipped+errors，且不影响整批
        mess = [{"title": "没有 url"}, "不是 dict", {"url": "https://example.gov.cn/c/3.html", "title": "正常一条"}]
        print(f"[check] 混合批 result={db.upsert_records(conn, KEY, NAME, mess, batch_3)}")
        dbcheck.execute(conn, f"DELETE FROM {dbcheck.qtable(KEY)} WHERE url=%s",  # nosec B608
                        ("https://example.gov.cn/c/3.html",))
        print(f"[check] 清掉临时那行后 row_count={dbcheck.count_rows(conn, KEY)}")

        for bad in ("People", "a-b", "", "中文", "a b", "tbl;DROP", "a';--"):
            try:
                db.table_name(bad)
            except ValueError:
                print(f"[check] 拒绝非法 key {bad!r} -> OK")
            else:
                print(f"[check] !! 未拒绝非法 key {bad!r}")
        print(f"[check] 合法 key people -> {db.table_name('people')}")
        print(f"[check] 自测表保留在 newscrawl.{TABLE}，供 mysql.exe 复核")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
