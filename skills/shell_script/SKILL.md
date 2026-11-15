---
name: shell_script
description: 把要执行的脚本先写入工作区，再用 run_shell 执行，并报告退出码。
when-to-use: 用户要求编写并运行脚本、执行命令、跑测试或查看某条命令的输出时。
---

脚本正文不要放进 `run_shell` 的 `command`。`run_shell` 只接受单条命令，不能包含换行，也不能使用 `bash -c`。

顺序：

1. 用 `write_file` 把脚本写到工作区，例如 `scripts/task.sh` 或 `scripts/task.py`。覆盖已有文件前必须先 `read_file`。
2. 脚本里如果包含 `sudo`、`rm`、`kill`、管道、重定向或命令替换，停下来说明，不要执行。
3. 用 `run_shell` 执行单条命令。Shell 脚本用 `bash scripts/task.sh`，Python 用 `python scripts/task.py`。不要加管道、重定向、分号或命令替换。
4. 把 `exit=` 和输出原样告诉用户。退出码不是 0 时不要说成成功，也不要自动重试。
5. 工具返回「当前权限不能执行 Shell」时，告诉用户改用 `--accept-edits` 重新启动。

不要用 `skill` 的 `fork` 模式执行本技能。`fork` 是只读的，不能调用 `run_shell`。
