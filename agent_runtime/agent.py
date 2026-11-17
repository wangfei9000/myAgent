"""Agent 循环：组工具、做权限检查、请求模型、写会话。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import browser, skill_exec, subagent, workspace
from .config import (
    MAX_TOOL_CHARS,
    PERMISSION_MODES,
    PROVIDERS,
    READ_ONLY_TOOLS,
)
from .mcp import McpManager, PLAYWRIGHT_RESULT_CHARS
from .model import OpenAIClient
from .skills import discover_skills, format_skill_hits, retrieve_skills
from .util import asks_for_another_tool, clip, load_dotenv

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]


class Agent:
    def __init__(
        self,
        root: Path,
        client: Any,
        permission_mode: str = "default",
        max_rounds: int = 12,
        enable_mcp: bool = True,
        save_session: bool = True,
        quiet: bool = False,
        depth: int = 0,
        system_prompt: str | None = None,
        allowed_tools: set[str] | None = None,
        user_skill_dir: Path | None = None,
    ):
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"未知权限模式：{permission_mode}")
        self.root = root.resolve()
        self.client = client
        self.permission_mode = permission_mode
        self.max_rounds = max_rounds
        self.enable_mcp = enable_mcp
        self.save_session = save_session
        self.quiet = quiet
        self.depth = depth
        self.system_override = system_prompt
        self.allowed_tools = allowed_tools
        self.user_skill_dir = user_skill_dir
        self.history: list[dict[str, Any]] = []
        self.logs: list[str] = []
        self.read_state: dict[str, int] = {}
        self._last_call_signature = ""
        self._consecutive_repeats = 0
        self._last_page_url = ""
        self._screenshot_seq = 0
        self.screenshot_dir = "screenshots/model"
        self.mcp = McpManager(self.root)
        self._mcp_ready = not enable_mcp
        self.skills = discover_skills(self.root, self.user_skill_dir)
        self.tool_map = {tool.name: tool for tool in self._build_tools()}

    def close(self) -> None:
        self.mcp.close()

    def emit(self, text: str) -> None:
        self.logs.append(text)
        if not self.quiet:
            print("  " * self.depth + text, flush=True)

    def refresh_skills(self) -> None:
        self.skills = discover_skills(self.root, self.user_skill_dir)

    def resolve_inside(self, raw: str) -> Path:
        return workspace.resolve_inside(self, raw)

    def authorize(self, name: str, args: dict[str, Any]) -> str | None:
        if self.permission_mode == "plan" and name not in READ_ONLY_TOOLS | {"skill", "agent"}:
            return "权限拒绝：当前是 plan 模式，只能读文件、查看技能或派发只读子 Agent"
        if name == "run_shell" and self.permission_mode != "accept-edits":
            return "权限拒绝：当前权限不能执行 Shell。需要时用 --accept-edits 启动"
        if name.startswith("mcp__") and self.permission_mode == "plan":
            return "权限拒绝：plan 模式不能调用 MCP 工具"
        if name == "agent" and self.depth >= 1:
            return "权限拒绝：子 Agent 不能再创建子 Agent"
        if name == "run_shell":
            return workspace.authorize_shell(str(args.get("command", "")))
        return None

    def ensure_mcp(self) -> None:
        if self._mcp_ready:
            return
        self._mcp_ready = True
        for note in self.mcp.load_and_connect():
            self.emit(note)

    def visible_tools(self) -> list[Tool]:
        self.ensure_mcp()
        names = list(self.tool_map)
        if self.allowed_tools is not None:
            names = [name for name in names if name in self.allowed_tools]
        else:
            if self.depth > 0:
                names = [name for name in names if name != "agent"]
            if self.permission_mode == "plan":
                names = [name for name in names if name in READ_ONLY_TOOLS or name == "skill"]
            elif self.permission_mode != "accept-edits":
                names = [name for name in names if name != "run_shell"]
        tools = [self.tool_map[name] for name in names]
        if self.allowed_tools is None and self.permission_mode != "plan":
            for spec in self.mcp.definitions:
                tools.append(
                    Tool(spec["name"], spec["description"], spec["parameters"], self._mcp_handler)
                )
        return tools

    def tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for tool in self.visible_tools():
            parameters = tool.parameters
            if tool.name == "agent":
                parameters = json.loads(json.dumps(parameters))
                parameters["properties"]["agent_type"]["enum"] = subagent.agent_types(self)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": parameters,
                    },
                }
            )
        return schemas

    def _mcp_handler(self, args: dict[str, Any]) -> str:
        raise RuntimeError("MCP 工具必须走路由表")

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name not in self.tool_map and name not in self.mcp.routes:
                return f"未知工具：{name}"
            denied = self.authorize(name, args)
            if denied:
                return denied
            if name in self.mcp.routes:
                result = self.mcp.call(name, args)
                result = browser.capture_new_page(self, name, result)
                server, _tool = self.mcp.routes[name]
                limit = PLAYWRIGHT_RESULT_CHARS if server == "playwright" else MAX_TOOL_CHARS
                return clip(result, limit)
            return clip(self.tool_map[name].handler(args))
        except PermissionError as exc:
            return f"权限拒绝：{exc}"
        except subprocess.TimeoutExpired:
            return "工具执行失败：命令超时"
        except (ValueError, FileNotFoundError, NotADirectoryError, OSError) as exc:
            return f"工具参数错误：{exc}"
        except Exception as exc:
            return f"工具执行失败：{type(exc).__name__}: {exc}"

    def system_prompt_text(self) -> str:
        if self.system_override:
            return self.system_override
        if self.skills:
            skill_lines = "\n".join(
                f"- {skill.name}: {skill.description or '无描述'}" for skill in self.skills
            )
        else:
            skill_lines = "（暂无。把 SKILL.md 放到 skills/<名称>/ 即可）"
        return f"""你是工作区里的通用 Agent。你只负责推理和提出工具调用；读写文件、执行命令、加载技能、调用 MCP 和派子 Agent 都由运行时完成，并先做权限检查。

工作区：{self.root}
权限模式：{self.permission_mode}
- plan：只能读文件、检索和查看技能
- default：可以修改工作区内文件，不能执行 Shell
- accept-edits：可以修改工作区内文件，并执行不含管道和危险程序的单条命令

规则：
- 涉及当前工作区的事实时先读文件或搜索，不要凭空编造。
- 修改已有文件前必须先 read_file；edit_file 的原文必须能唯一匹配。
- 路径必须留在工作区内。密钥文件不可读、不可写。
- 需要可复用方法时调用 skill。inline 返回 SKILL.md 正文；fork 开一个只读子任务按技能执行。
- 大范围找代码时调用 agent，类型用 explore，避免把无关文件堆进当前对话。
- 拿到足够结果后直接回答，不要重复同一个工具调用。
- 用简体中文回答。

可用技能：
{skill_lines}

子 Agent：explore（只读搜索）、plan（只读计划）、general（继承当前权限，不能再派生子 Agent）。
MCP 工具会以 mcp__服务器__工具名 出现在工具列表里。"""

    def chat(self, user_text: str, *, augment_skills: bool = True, skill: str | list[str] | None = None) -> str:
        self._last_call_signature = ""
        self._consecutive_repeats = 0
        content = user_text
        if isinstance(skill, str):
            skill_names = [part.strip() for part in skill.split(",") if part.strip()]
        elif isinstance(skill, list):
            skill_names = [str(part).strip() for part in skill if str(part).strip()]
        else:
            skill_names = []
        if skill_names:
            self.refresh_skills()
            loaded_parts = []
            for name in skill_names:
                loaded = skill_exec.execute_skill(self, {"name": name, "mode": "inline"})
                if loaded.startswith("没有名为"):
                    raise ValueError(loaded)
                loaded_parts.append(loaded)
            content = user_text + "\n\n" + "\n\n".join(loaded_parts)
        elif augment_skills:
            self.refresh_skills()
            hint = format_skill_hits(retrieve_skills(self.skills, user_text))
            if hint:
                content = user_text + "\n\n" + hint
        self.history.append({"role": "user", "content": content})
        final = ""
        tool_nudges = 0
        for round_index in range(1, self.max_rounds + 1):
            self.emit(f"[第 {round_index} 轮] 正在请求模型...")
            turn = self.client.complete(self._request_messages(), self.tool_schemas())
            assistant: dict[str, Any] = {"role": "assistant", "content": turn.content or ""}
            if turn.tool_calls:
                assistant["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                        },
                    }
                    for call in turn.tool_calls
                ]
            self.history.append(assistant)
            if turn.content:
                self.emit(f"[第 {round_index} 轮] {clip(turn.content, 400)}")
            if not turn.tool_calls:
                if tool_nudges < 3 and asks_for_another_tool(turn.content or ""):
                    tool_nudges += 1
                    self.history.append(
                        {
                            "role": "user",
                            "content": "你这一轮没有调用工具，任务还没做完。请立刻调用下一个工具，不要只用文字描述。",
                        }
                    )
                    self.emit(f"[第 {round_index} 轮] 未调用工具，继续下一轮")
                    continue
                final = turn.content or "（模型没有返回文本）"
                break
            stop = False
            for call in turn.tool_calls:
                signature = json.dumps(
                    [call["name"], call["arguments"]], ensure_ascii=False, sort_keys=True
                )
                if signature == self._last_call_signature:
                    self._consecutive_repeats += 1
                else:
                    self._last_call_signature = signature
                    self._consecutive_repeats = 1
                shown = json.dumps(call["arguments"], ensure_ascii=False)
                self.emit(f"[第 {round_index} 轮] 调用 {call['name']} {clip(shown, 240)}")
                if self._consecutive_repeats >= 4:
                    result = "已中断：相同工具和参数连续重复了 4 次。请根据已有结果直接回答。"
                    stop = True
                else:
                    result = self.dispatch(call["name"], call["arguments"])
                self.emit(f"[第 {round_index} 轮] 结果 {clip(result, 300)}")
                self.history.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
            if stop:
                final = "相同工具调用连续重复次数过多，循环已停止。请缩小问题或更换权限模式后重试。"
                break
        else:
            final = "达到最大轮次，Agent 停止。可以增大 --max-rounds，或先 /compact 再继续。"
        if self.save_session and self.depth == 0:
            self._write_session()
        return final

    def _request_messages(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.system_prompt_text()}, *self.history]

    def compact(self) -> str:
        compacted = []
        for message in self.history[-8:]:
            if message.get("role") == "tool" and len(message.get("content", "")) > 500:
                compacted.append({**message, "content": message["content"][:500] + "\n...[compacted]"})
            else:
                compacted.append(message)
        self.history = compacted
        return f"上下文已折叠，保留最近 {len(self.history)} 条消息。"

    def _write_session(self) -> None:
        path = self.root / "sessions" / "latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "permission_mode": self.permission_mode,
            "messages": self.history,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_session(self) -> str:
        path = self.root / "sessions" / "latest.json"
        if not path.is_file():
            return "没有可恢复的会话。"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.history = list(payload.get("messages") or [])
        return f"已恢复 {len(self.history)} 条消息。"

    def _build_tools(self) -> list[Tool]:
        return [
            Tool(
                "read_file",
                "读取工作区内的文本文件，返回带行号的内容。",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对工作区的路径"},
                        "offset": {"type": "integer", "description": "从 1 开始的行号"},
                        "limit": {"type": "integer", "description": "最多读取的行数，默认 200"},
                    },
                    "required": ["path"],
                },
                lambda args: workspace.read_file(self, args),
            ),
            Tool(
                "write_file",
                "在工作区内创建或覆盖文本文件。覆盖已有文件前必须先 read_file。",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                lambda args: workspace.write_file(self, args),
            ),
            Tool(
                "edit_file",
                "把文件中唯一匹配的原文替换成新文。调用前必须先 read_file。",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                lambda args: workspace.edit_file(self, args),
            ),
            Tool(
                "list_files",
                "按 glob 列出工作区内的文件。",
                {
                    "type": "object",
                    "properties": {"pattern": {"type": "string", "description": "例如 *.py 或 rag/*.md"}},
                    "required": ["pattern"],
                },
                lambda args: workspace.list_files(self, args),
            ),
            Tool(
                "grep_search",
                "在工作区文本里搜索正则表达式，返回 文件:行号:内容。",
                {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "glob": {"type": "string", "description": "可选，例如 *.py"},
                    },
                    "required": ["pattern"],
                },
                lambda args: workspace.grep_search(self, args),
            ),
            Tool(
                "run_shell",
                "在工作区执行单条命令。不能使用管道、重定向、sudo、rm。仅 accept-edits 可用。",
                {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
                lambda args: workspace.run_shell(self, args),
            ),
            Tool(
                "skill",
                "按名称加载 SKILL.md。mode=inline 返回正文；mode=fork 用只读子任务执行该技能。",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "mode": {"type": "string", "enum": ["inline", "fork"]},
                        "task": {"type": "string", "description": "fork 时交给子任务的具体问题"},
                    },
                    "required": ["name"],
                },
                lambda args: skill_exec.execute_skill(self, args),
            ),
            Tool(
                "skill_create",
                "把可复用方法写成项目级 skills/<名称>/SKILL.md。",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "when_to_use": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["name", "description", "body"],
                },
                lambda args: skill_exec.create_skill(self, args),
            ),
            Tool(
                "agent",
                "派一个上下文隔离的子 Agent。explore/plan 只读，general 继承当前权限。",
                {
                    "type": "object",
                    "properties": {
                        "agent_type": {"type": "string", "enum": ["explore", "plan", "general"]},
                        "task": {"type": "string"},
                    },
                    "required": ["agent_type", "task"],
                },
                lambda args: subagent.run_subagent(self, args),
            ),
        ]


def create_agent(
    root: str | Path = ".",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    permission_mode: str = "default",
    max_rounds: int = 12,
    enable_mcp: bool = True,
    save_session: bool = True,
    quiet: bool = False,
    timeout: float = 300,
    client: Any = None,
    user_skill_dir: Path | None = None,
    log_model: bool = False,
) -> Agent:
    """创建一个绑定到工作区的 Agent。传入 client 时可替换模型实现。"""
    root_path = Path(root).resolve()
    load_dotenv(root_path)
    if client is None:
        log_path = None
        if log_model:
            log_path = root_path / "logs" / "model.log"
        provider = PROVIDERS[0]
        client = OpenAIClient(
            base_url=base_url or provider["base_url"],
            api_key=api_key if api_key is not None else provider["api_key"],
            model=model or provider["model"],
            timeout=timeout,
            log_path=log_path,
        )
    return Agent(
        root=root_path,
        client=client,
        permission_mode=permission_mode,
        max_rounds=max_rounds,
        enable_mcp=enable_mcp,
        save_session=save_session,
        quiet=quiet,
        user_skill_dir=user_skill_dir,
    )
