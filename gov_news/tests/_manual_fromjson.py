"""核对 crawl.py --from-json 路径：dump → 抹掉业务字段 → 回灌 → 验证值被还原。

    uv run --isolated --extra ai --with pymysql python gov_news/tests/_manual_fromjson.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

GOV_NEWS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOV_NEWS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import dbcheck  # noqa: E402

KEY = "db_fromjson_selftest"
NAME = "回灌自测源(假数据)"
TABLE = db.table_name(KEY)
DUMP = GOV_NEWS / "out" / "_fromjson_selftest.json"
TARGET = "https://example.gov.cn/fromjson/1.html"

ARTICLES = [
    {
        "source": KEY,
        "source_name": NAME,
        "url": TARGET,
        "title": "回灌第一条：<em>南海</em>不该带标签",
        "body": "正文" * 5,
        "body_length": 10,
        "published": "2026-11-20T13:02:14",
        "summary": "回灌摘要一",
        "category": "海洋权益",
        "matched_keywords": ["南海", "仁爱礁"],
        "match_level": "server",
        "http_status": 200,
        "image_urls": ["https://img/1.jpg", "https://img/2.jpg"],
    },
    {
        "source": KEY,
        "source_name": NAME,
        "url": "https://example.gov.cn/fromjson/2.html",
        "title": "回灌第二条",
        "body": "正文乙" * 4,
        "body_length": 12,
        "publisher": "人民网",
    },
]


def main() -> int:
    DUMP.parent.mkdir(parents=True, exist_ok=True)
    DUMP.write_text(json.dumps({"meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                         "batch_time": "2026-11-20T13:02:14",
                                         "sources": [KEY]},
                                "articles": ARTICLES}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dump] {DUMP} 条数={len(ARTICLES)}")

    conn = db.connect(db_name="newscrawl")
    try:
        dbcheck.drop_table(conn, KEY)
        db.ensure_schema(conn, KEY, NAME)
    finally:
        conn.close()

    # 真的走 crawl.py 的 --from-json 分支（它按契约调 db.load_records_from_json）
    cmd = [sys.executable, str(GOV_NEWS / "crawl.py"), "--from-json", str(DUMP)]
    first = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    print(f"[crawl --from-json] exit={first.returncode}")
    print(first.stdout.strip())
    if first.stderr.strip():
        print(f"[stderr] {first.stderr.strip()[:400]}")

    conn = db.connect(db_name="newscrawl")
    try:
        print(f"[check] 行数={dbcheck.count_rows(conn, KEY)}")
        for row in dbcheck.select(conn, KEY, "ORDER BY url"):
            print(f"[check] row={row}")

        dbcheck.execute(conn, f"UPDATE {dbcheck.qtable(KEY)} SET title='已抹掉', summary=NULL, "  # nosec B608
                              "matched_keywords=NULL, image_urls=NULL, publish_date=NULL, batch_time=NULL "
                              "WHERE url=%s", (TARGET,))
        wiped = dbcheck.rows_from(conn, KEY, "title", where="url=%s", params=(TARGET,))[0]["title"]
        print(f"[check] 抹掉后 title={wiped!r}")

        second = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        print(f"[crawl --from-json 二次] exit={second.returncode}")
        print(second.stdout.strip())
        # MySQL 默认 REPEATABLE READ：不 commit 就会一直读旧快照
        conn.commit()
        restored = dbcheck.rows_from(conn, KEY, "title, matched_keywords, image_urls, publish_date, batch_time",
                                     where="url=%s", params=(TARGET,))[0]
        print(f"[check] 回灌后 row={restored}")
        print(f"[check] 行数仍为 {dbcheck.count_rows(conn, KEY)}")

        dbcheck.drop_table(conn, KEY)
        print(f"[check] 自测表 {TABLE} 已清理")
    finally:
        conn.close()
    DUMP.unlink(missing_ok=True)
    print(f"[check] 临时 dump 已清理：{DUMP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
