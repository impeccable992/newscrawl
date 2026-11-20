"""gov_news 入库测试的公共装置。

`db.py` 与 `crawl.py` 同目录，靠脚本目录导入（`crawl.py` 就是这么 `import db` 的），
所以这里把 `gov_news/` 与 `gov_news/tests/` 塞进 `sys.path`，测试里直接 `import db` / `import dbcheck`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
GOV_NEWS = TESTS_DIR.parent
for path in (str(GOV_NEWS), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import db  # noqa: E402  （路径补好之后再导入）
import dbcheck  # noqa: E402

from dbcheck import TEST_KEY, TEST_NAME  # noqa: E402


@pytest.fixture(scope="session")
def conn():
    """真实 MySQL 连接；连不上就 skip（并在 skip 原因里写清怎么跑）。"""
    try:
        connection = db.connect(db_name="newscrawl")
    except Exception as exc:  # noqa: BLE001 - 只用来决定 skip 还是继续
        pytest.skip(f"MySQL 127.0.0.1:3306 不可用，跳过实库测试：{type(exc).__name__}: {exc}")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def fresh_table(conn):
    """每用例一张干净的自测表，用例结束后删掉。"""
    dbcheck.drop_table(conn, TEST_KEY)
    db.ensure_schema(conn, TEST_KEY, TEST_NAME)
    try:
        yield db.table_name(TEST_KEY)
    finally:
        dbcheck.drop_table(conn, TEST_KEY)
