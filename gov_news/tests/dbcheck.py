"""测试与实跑脚本共用的断言工具（表名来自白名单校验的 `db.table_name()`）。

这个模块是测试代码里唯一拼 SQL 的地方：表名只可能是 `ods_<已验证 key>_news`，
所有数据值一律走 `%s` 占位符，所以模块内统一标注 B608。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import db

# PyMySQL 2.x 不把 DictCursor 导出到顶层，得从 cursors 子模块拿
from pymysql.cursors import DictCursor

TEST_KEY = "db_selftest"          # 独立命名空间，不碰真实源的表（people / ccg / ...）
TEST_NAME = "自测源(假数据)"


def qtable(source_key: str, db_name: str = "newscrawl") -> str:
    """限定表名；两侧都过 db.py 的白名单校验。"""
    return f"`{db.db_name_ok(db_name)}`.`{db.table_name(source_key)}`"  # nosec B608


def info_query(conn: Any, sql: str, params: Optional[Sequence[Any]] = None) -> list:
    """查元数据；表名这类值走 %s 参数，不拼进 SQL。"""
    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_scalar(conn: Any, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
    """取单个值。"""
    with conn.cursor() as cur:
        cur.execute(sql, params)  # nosec B608
        row = cur.fetchone()
        return row[0] if row is not None else None


def fetch_rows(conn: Any, sql: str, params: Optional[Sequence[Any]] = None) -> list:
    """读出带列名的结果（DictCursor：打印出来自带字段名，便于核对）。"""
    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, params)  # nosec B608
        return cur.fetchall()


def execute(conn: Any, sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """执行写语句并提交（测试里要显式提交，否则 REPEATABLE READ 会一直读旧快照）。"""
    with conn.cursor() as cur:
        cur.execute(sql, params)  # nosec B608
        affected = cur.rowcount
    conn.commit()
    return affected


def rows_from(conn: Any, source_key: str, columns: str, where: str = "",
              params: Optional[Sequence[Any]] = None) -> list:
    """从自测表读行；columns/where 由调用方写成字面量，表名走白名单，值走 %s 占位符。"""
    sql = f"SELECT {columns} FROM {qtable(source_key)}" + (f" WHERE {where}" if where else "")  # nosec B608
    return fetch_rows(conn, sql, params)


def select(conn: Any, source_key: str, suffix: str = "") -> list:
    """从自测表读整行（suffix 由调用方写成字面量，如表名后的 ORDER BY）。"""
    sql = f"SELECT * FROM {qtable(source_key)} {suffix}".rstrip()  # nosec B608
    return fetch_rows(conn, sql)


def count_rows(conn: Any, source_key: str) -> int:
    return fetch_scalar(conn, f"SELECT COUNT(*) FROM {qtable(source_key)}")  # nosec B608


def drop_table(conn: Any, source_key: str) -> None:
    execute(conn, f"DROP TABLE IF EXISTS {qtable(source_key)}")  # nosec B608
