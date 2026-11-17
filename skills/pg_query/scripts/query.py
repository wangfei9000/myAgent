#!/usr/bin/env python3
"""执行一条只读 PostgreSQL 查询。连接串来自环境变量，不打印。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.pg import QueryError, run_query


def main() -> None:
    try:
        if len(sys.argv) != 2:
            raise QueryError("用法：python3 skills/pg_query/scripts/query.py <sql文件>")
        sql_path = Path(sys.argv[1])
        if not sql_path.is_file():
            raise QueryError(f"找不到 SQL 文件：{sql_path}", 1)
        print(run_query(sql_path.read_text(encoding="utf-8")))
    except QueryError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(exc.code) from exc


if __name__ == "__main__":
    main()
