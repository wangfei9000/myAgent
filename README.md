# Agent Runtime

工作区里的通用 Agent。模型只负责推理和提出工具调用；读文件、改文件、执行命令、加载技能、调用 MCP 和派子 Agent 都由运行时完成，并先做权限检查。

对接 OpenAI 兼容的 `/chat/completions`。不依赖 `openai` 包，标准库即可跑 HTTP 服务。浏览器能力通过本机的 Playwright MCP 接入。

## 启动

在项目根目录执行：

```bash
python3 server.py
```

默认监听 `http://127.0.0.1:8080`。常用参数：

```bash
python server.py --host 127.0.0.1 --port 8080 --permission default
python server.py --model deepseek-flash --base-url https://api.deepseek.com --api-key "$OPENAI_API_KEY"
python server.py --no-mcp
python server.py --root /path/to/workspace --log-model
python -m agent_runtime.cli --log-model --max-rounds 40 --skill oem3_page '先进行登录，然后点击建筑物菜单，搜索五栋大楼小区，然后打开小区详情，返回小区id'
```

| 参数 | 默认 | 作用 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址 |
| `--port` | `8080` | 监听端口 |
| `--permission` | `default` | `plan`、`default`、`accept-edits` |
| `--model` | 见下方配置 | 模型名 |
| `--base-url` | 见下方配置 | OpenAI 兼容接口根地址 |
| `--api-key` | 见下方配置 | 接口密钥 |
| `--max-rounds` | `12` | 单次对话最多轮数 |
| `--timeout` | `300` | 单次模型请求最长等待秒数 |
| `--root` | 项目根目录 | Agent 的工作区 |
| `--no-mcp` | 关闭 | 不连接 MCP |
| `--log-model` | 关闭 | 把发给模型和模型返回的文字写入 `logs/model.log` |

进程里只有一个 Agent。`/api/chat` 会串行执行，历史在进程内累积。启动时如果已有 `logs/model.log`，会把它和 `screenshots/model/` 归档成带序号的上一轮记录。

终端对话仍可用：

```bash
python -m agent_runtime.cli
python -m agent_runtime.cli --plan "只读看看这个项目"
python -m agent_runtime.cli --skill project_brief "这个项目在做什么"
python -m agent_runtime.cli --accept-edits "跑一条命令并记下结果"
python -m agent_runtime.cli --resume
```

`--plan` 和 `--accept-edits` 只在命令行里有，不能同时使用。HTTP 用 `--permission` 指定同一种模式。

命令行里不给任务就进入 REPL。可用命令：`/skill 名称 [任务]`、`/plan`、`/default`、`/accept-edits`、`/skills`、`/tools`、`/compact`、`/quit`。

## HTTP

打开 `http://127.0.0.1:8080` 就是任务页面。左侧选择服务（DeepSeek 或 Ollama）、填写任务，然后开始执行或清空对话。权限固定为 `default`，最大轮次固定为 100，页面上不显示这两项。右侧刷新执行过程、最终结果，以及这次任务新产生的页面截图。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 任务页面 `web/index.html` |
| `GET` | `/api/health` | `{"ok": true}` |
| `GET` | `/api/status` | 当前模型、权限、轮次、是否在执行 |
| `GET` | `/api/skills` | 可用技能 |
| `GET` | `/api/run` | 当前任务的过程、结果、错误和截图 |
| `POST` | `/api/chat` | 开始一个任务，立刻返回，不等模型结束 |
| `POST` | `/api/reset` | 清空当前对话。任务执行中会拒绝 |
| `GET` | `/screenshots/<文件名>` | 本次工作区 `screenshots/model/` 里的截图 |

`POST /api/chat` 的请求体：

```json
{"message": "先登录，再打开建筑物菜单", "skill": "oem3_page", "permission": "default", "max_rounds": 40}
```

`skill` 可省略。任务已开始时返回 202。已有任务在执行时返回 409。缺 `message`、请求体不是 JSON，返回 400。

浏览器操作需要 `default` 或 `accept-edits`。`plan` 不能调用 MCP。

## 模型配置

优先级：命令行参数、环境变量、`agent_runtime/config.py` 里的默认值。

| 环境变量 | 含义 |
|---|---|
| `OPENAI_BASE_URL` | 接口根地址，例如 `https://api.deepseek.com` 或 `http://127.0.0.1:11434/v1` |
| `OPENAI_API_KEY` | 密钥。本机 Ollama 可以不设 |
| `OPENAI_MODEL` | 模型名 |

启动时会读当前目录和工作区里的 `.env`。已经存在的环境变量不会被 `.env` 覆盖。密钥放在 `.env` 或环境变量里，不要写进会提交的文件。

## 权限

| 模式 | 能做的事 |
|---|---|
| `plan` | 读文件、查看技能、派只读子 Agent |
| `default` | 在工作区内改文件。不能执行 Shell，也不能在 plan 下调用 MCP |
| `accept-edits` | 可以改文件，并执行单条命令 |

Shell 不允许管道、重定向、命令替换，也不允许 `sudo`、`rm` 等危险程序。路径必须留在工作区内。`.env`、私钥和证书文件不可读、不可写。覆盖或编辑已有文件之前必须先 `read_file`；文件在读取后变过，要重新读。

## 工具

模型可以调用：

- `read_file`、`list_files`、`grep_search`：读和搜索
- `write_file`、`edit_file`：写文件
- `run_shell`：仅 `accept-edits`
- `skill`：加载技能。`inline` 把正文放进当前对话，`fork` 开只读子任务执行
- `skill_create`：写成 `skills/<名称>/SKILL.md`
- `agent`：派子 Agent。`explore` 和 `plan` 只读，`general` 继承当前权限。子 Agent 不能再派子 Agent
- `mcp__服务器__工具名`：外部 MCP 工具

相同工具和相同参数连续重复 4 次会停下来。

## 技能

技能是带 frontmatter 的 `SKILL.md`。项目级放在 `skills/<名称>/`，用户级放在 `~/skills/<名称>/`。同名时项目级覆盖用户级。

每次对话会按问题检索最多 3 个相关技能，只把名称和说明放进上下文。需要完整步骤时，模型再调用 `skill`。

当前项目里有：`project_brief`、`code_review`、`browser`、`shell_script`、`rag_lookup`、`oem3_page`。

自定义子 Agent 放在工作区的 `agents/*.md`，或用户技能目录旁边的 `agents/*.md`。`explore`、`plan`、`general` 这三个名字保留给内置类型。

## MCP

配置读 `.mcp.json` 或 `settings.json` 里的 `mcpServers`。项目根目录和 `--root` 都会看，先读到的同名服务会被后读到的覆盖。当前仓库的 `.mcp.json` 启动本机 Playwright：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "node",
      "args": ["node_modules/@playwright/mcp/cli.js", "--headless", "--timeout-settle", "3000"]
    }
  }
}
```

第一次没有 `node_modules/@playwright/mcp/cli.js` 时，运行时会执行 `npm install`。也可以自己安装：

```bash
npm install
```

Playwright 只把这些工具交给模型：`browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、`browser_press_key`、`browser_wait_for`、`browser_take_screenshot`。地址变化后，运行时会等页面稳定，截图到 `screenshots/model/`，再把新快照交给下一步。

## 代码在哪

| 路径 | 内容 |
|---|---|
| `server.py` | HTTP 入口 |
| `agent_runtime/cli.py` | 命令行和 REPL |
| `agent_runtime/agent.py` | 对话循环、权限、工具表、会话 |
| `agent_runtime/model.py` | OpenAI 兼容客户端 |
| `agent_runtime/skills.py` | 发现和检索技能 |
| `agent_runtime/skill_exec.py` | 执行和创建技能 |
| `agent_runtime/subagent.py` | 子 Agent |
| `agent_runtime/workspace.py` | 工作区读写、搜索、Shell |
| `agent_runtime/mcp.py` | MCP 连接和工具路由 |
| `agent_runtime/browser.py` | 页面稳定后截图，以及归档上一轮日志 |
| `agent_runtime/config.py` | 常量、权限模式、模型默认值 |
| `agent_runtime/util.py` | dotenv、frontmatter、分词、路径判断 |

会话写在工作区的 `sessions/latest.json`。命令行用 `--resume` 恢复。HTTP 启动时不恢复，但对话过程仍会覆盖这份会话文件。

需要 Python 3.10 或更新版本。要用浏览器工具时再装 Node.js。
