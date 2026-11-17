"""只读 PostgreSQL 的 MCP stdio 服务。标准输出只写一行一个 JSON。"""

from __future__ import annotations

import sys

from .mcp import read_mcp_message, write_mcp_message
from .pg import QueryError, run_query, scrub

PROTOCOL = "2024-11-05"
QUERY_TOOL = {
    "name": "query",
    "description": "对 PostgreSQL 执行一条只读 SQL（SELECT、WITH、SHOW、EXPLAIN、TABLE、VALUES）。写入语句会被拒绝，最多返回 100 行。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "一条 SELECT、WITH、SHOW、EXPLAIN、TABLE 或 VALUES",
            }
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
}


def tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_query(arguments: object) -> dict:
    if not isinstance(arguments, dict):
        return tool_result("缺少 sql", True)
    sql = arguments.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return tool_result("缺少 sql", True)
    try:
        return tool_result(run_query(sql))
    except QueryError as exc:
        return tool_result(str(exc), True)
    except Exception as exc:
        return tool_result(scrub(f"查询失败：{type(exc).__name__}: {exc}"), True)


def handle(method: str, params: object) -> dict:
    if not isinstance(params, dict):
        params = {}
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "postgres", "version": "0.1.0"},
        }
    if method == "tools/list":
        return {"tools": [QUERY_TOOL]}
    if method == "ping":
        return {}
    if method == "tools/call":
        name = params.get("name")
        if name != "query":
            return tool_result(f"未知工具：{name}", True)
        return call_query(params.get("arguments"))
    raise KeyError(method)


def serve() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        try:
            message = read_mcp_message(stdin)
        except Exception as exc:
            print(scrub(f"MCP 消息无法解析：{exc}"), file=sys.stderr)
            return
        if message is None:
            return
        if not isinstance(message, dict):
            continue
        msg_id = message.get("id")
        if msg_id is None:
            continue
        method = str(message.get("method") or "")
        try:
            result = handle(method, message.get("params") or {})
        except KeyError:
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"未知方法：{method}"},
            }
        except Exception as exc:
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": scrub(f"{type(exc).__name__}: {exc}")},
            }
        else:
            payload = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        try:
            write_mcp_message(stdout, payload)
        except BrokenPipeError:
            return


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
