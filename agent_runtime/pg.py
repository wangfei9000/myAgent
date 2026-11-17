"""只读 PostgreSQL 查询。连接串来自环境变量，异常信息里的口令会被抹掉。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import script_dir
from .util import load_dotenv

ROW_LIMIT = 100
STATEMENT_TIMEOUT = "20s"
ALLOWED_START = ("select", "with", "show", "explain", "table", "values")
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|copy|vacuum|reindex|into)\b",
    re.IGNORECASE,
)


class QueryError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def scrub(text: str) -> str:
    return re.sub(r"(://[^:\s/]+:)[^@\s/]+@", r"\1***@", text)


def fail(message: str, code: int = 2) -> None:
    raise QueryError(scrub(message), code)


def load_env() -> None:
    load_dotenv(script_dir())
    load_dotenv(Path.cwd())


def statements(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    in_single = False
    in_double = False
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if not in_single and not in_double and ch == "-" and nxt == "-":
            i += 2
            while i < len(sql) and sql[i] != "\n":
                i += 1
            continue
        if not in_single and not in_double and ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            if end < 0:
                fail("SQL 注释没有闭合", 1)
            i = end + 2
            continue
        if ch == "'" and not in_double:
            if in_single and nxt == "'":
                buf.append("''")
                i += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            text = "".join(buf).strip()
            if text:
                parts.append(text)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if in_single or in_double:
        fail("SQL 引号没有闭合", 1)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def mask_literals(sql: str) -> str:
    masked = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return re.sub(r'"(?:""|[^"])*"', '""', masked)


def validate(sql: str) -> str:
    found = statements(sql)
    if not found:
        fail("SQL 为空", 1)
    if len(found) != 1:
        fail("只允许一条 SQL", 1)
    statement = found[0]
    first = statement.split(None, 1)[0].lower()
    if first not in ALLOWED_START:
        fail("只允许 SELECT、WITH、SHOW、EXPLAIN、TABLE 或 VALUES", 1)
    if FORBIDDEN.search(mask_literals(statement)):
        fail("语句含有写入或管理关键字，已拒绝", 1)
    return statement


def connection_target() -> str | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        if not url.startswith(("postgres://", "postgresql://")):
            fail("DATABASE_URL 必须是 postgresql:// 开头")
        return url
    if any(os.environ.get(name) for name in ("PGHOST", "PGUSER", "PGDATABASE")):
        return None
    fail("没有数据库连接。请设置 DATABASE_URL，或设置 PGHOST、PGUSER、PGDATABASE")
    return None


def connect(url: str | None):
    try:
        import psycopg
    except ImportError:
        psycopg = None
    if psycopg is not None:
        if url:
            return psycopg.connect(url, connect_timeout=10)
        return psycopg.connect(connect_timeout=10)
    try:
        import psycopg2
    except ImportError:
        fail('缺少 PostgreSQL 驱动。执行：python3 -m pip install "psycopg[binary]"')
    if url:
        return psycopg2.connect(url, connect_timeout=10)
    return psycopg2.connect(connect_timeout=10)


def column_names(description) -> list[str]:
    names = []
    for column in description or []:
        name = getattr(column, "name", None)
        if name is None:
            name = column[0]
        names.append(str(name))
    return names


def cell(value: object) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("\n", " ").replace("|", "/")
    if len(text) > 80:
        return text[:77] + "..."
    return text


def limited_sql(statement: str) -> str:
    if statement.split(None, 1)[0].lower() in {"show", "explain"}:
        return statement
    return f"SELECT * FROM (\n{statement}\n) AS _q\nLIMIT {ROW_LIMIT + 1}"


def format_result(names: list[str], rows: list, truncated: bool) -> str:
    lines = [
        "columns: " + (", ".join(names) if names else "（无）"),
        f"rows: {len(rows)}",
    ]
    if names:
        lines.append(" | ".join(names))
        for row in rows:
            lines.append(" | ".join(cell(value) for value in row))
    if truncated:
        lines.append(f"...[只显示前 {ROW_LIMIT} 行]")
    return "\n".join(lines)


def run_query(sql: str) -> str:
    """执行一条只读 SQL，返回文本结果。拒绝写入、多语句和缺失连接。"""
    load_env()
    statement = validate(sql)
    url = connection_target()
    try:
        conn = connect(url)
    except QueryError:
        raise
    except Exception as exc:
        fail(f"连接失败：{type(exc).__name__}: {exc}")
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
            cur.execute(limited_sql(statement))
            names = column_names(cur.description)
            rows = cur.fetchmany(ROW_LIMIT + 1)
    except QueryError:
        raise
    except Exception as exc:
        fail(f"查询失败：{type(exc).__name__}: {exc}", 1)
    finally:
        conn.close()

    truncated = len(rows) > ROW_LIMIT
    return format_result(names, rows[:ROW_LIMIT], truncated)
