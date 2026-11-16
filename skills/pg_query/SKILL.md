---
name: pg_query
description: 用只读 SQL 查询 PostgreSQL，先看表结构再查数据。
when-to-use: 用户要查 PostgreSQL、跑 SQL、看表结构、统计行数或核对某张表的数据时。
---

连接信息只从环境变量读取。不要写进 SQL 文件，不要打印，也不要读取 `.env`。

- `DATABASE_URL`：`postgresql://用户:密码@主机:5432/数据库名`
- 没设 `DATABASE_URL` 时，可以使用 `PGHOST`、`PGPORT`、`PGUSER`、`PGPASSWORD`、`PGDATABASE`

这些变量都没有时停下来，告诉用户先在环境变量或 `.env` 里设置。

只执行一条 `SELECT`、`WITH`、`SHOW`、`EXPLAIN`、`TABLE` 或 `VALUES`。遇到 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`DROP`、`ALTER`、`TRUNCATE`、`CREATE` 时停下来说明，不要写文件，也不要执行。

查询脚本是 `skills/pg_query/scripts/query.py`。不要改它。

顺序：

1. 用 `write_file` 把这一条 SQL 写到 `logs/pg_query.sql`。覆盖前先 `read_file`。
2. 用 `run_shell` 执行 `python3 skills/pg_query/scripts/query.py logs/pg_query.sql`。不要把 SQL 放进命令，也不要加管道、重定向、分号或 `bash -c`。
3. 把 `exit=` 和输出原样告诉用户。退出码不是 0 时不要说成成功，也不要自动换一条 SQL 重试。
4. 输出写明缺少驱动时，再执行一次 `python3 -m pip install psycopg[binary]`，然后重新执行第 2 步。仍然失败就停下来。
5. 工具返回「当前权限不能执行 Shell」时，告诉用户改用 `--accept-edits` 重新启动。

要看有哪些表时，查询 `information_schema.tables`，条件用 `table_schema = 'public'`。要看字段时，查询 `information_schema.columns`。

不要用 `skill` 的 `fork` 模式。`fork` 是只读的，不能调用 `run_shell`。
