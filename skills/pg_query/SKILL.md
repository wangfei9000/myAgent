---
name: pg_query
description: 用只读 SQL 查询 PostgreSQL，先看表结构再查数据。
when-to-use: 用户要查 PostgreSQL、跑 SQL、看表结构、统计行数或核对某张表的数据时。
---

用 MCP 工具 `mcp__postgres__query` 执行一条只读 SQL。参数名是 `sql`。

连接由 MCP 进程从环境变量读取。不要在对话、SQL 或文件里写连接串，不要打印，也不要读取 `.env`。

- `DATABASE_URL`：`postgresql://用户:密码@主机:5432/数据库名`
- 没设 `DATABASE_URL` 时，可以使用 `PGHOST`、`PGPORT`、`PGUSER`、`PGPASSWORD`、`PGDATABASE`

这些变量都没有时停下来，告诉用户先在环境变量或 `.env` 里设置。

只发送一条 `SELECT`、`WITH`、`SHOW`、`EXPLAIN`、`TABLE` 或 `VALUES`。遇到 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`DROP`、`ALTER`、`TRUNCATE`、`CREATE` 时停下来说明，不要调用工具。

顺序：

1. 调用 `mcp__postgres__query`，参数 `sql` 是这一条 SQL。
2. 把工具返回的原文告诉用户。返回里出现「查询失败」「连接失败」「已拒绝」时不要说成成功，也不要自动换一条 SQL 重试。
3. 返回写明缺少驱动时，再用 `run_shell` 执行一次 `python3 -m pip install psycopg[binary]`，然后重新执行第 1 步。仍然失败就停下来。`run_shell` 需要 `accept-edits`。
4. 工具不存在或提示 MCP 未连接时，告诉用户确认 `.mcp.json` 里有 `postgres`，并且启动时没有加 `--no-mcp`。

要看有哪些表时，查询 `information_schema.tables`，条件用 `table_schema = 'public'`。要看字段时，查询 `information_schema.columns`。

`plan` 模式不能调用 MCP。查询用 `default` 或 `accept-edits`。

不要用 `skill` 的 `fork` 模式。子 Agent 不连接 MCP。不要把 SQL 写进文件，也不要用 `run_shell` 执行 `skills/pg_query/scripts/query.py`。
