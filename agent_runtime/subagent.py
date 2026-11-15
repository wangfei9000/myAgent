"""子 Agent：内置 explore / plan / general，以及 agents/*.md 里的自定义类型。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import READ_ONLY_TOOLS
from .util import parse_frontmatter

EXPLORE_PROMPT = """你是只读的代码搜索子 Agent。只能使用 read_file、list_files、grep_search。
不要修改文件，也不要提出需要执行命令的步骤当作已经完成。
先定位再阅读，最后用简体中文给出：找到的文件、关键行号、和问题直接相关的结论。"""

PLAN_PROMPT = """你是只读的计划子 Agent。只能阅读工作区，不能修改文件。
输出简体中文计划，包含：现状、步骤、会改到的文件、风险。不要假装已经改完。"""

GENERAL_PROMPT = """你是承担局部任务的子 Agent。完成任务后用简体中文简短汇报做了什么、结果是什么。
不要再创建子 Agent。能改已有文件时不要另起新文件。"""

FORK_PROMPT = """你在一个只读子任务里执行下面的技能。请严格按技能步骤阅读工作区，然后给出结论。
不要修改文件。"""


def custom_agents(agent) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    directories = []
    if agent.user_skill_dir is not None:
        directories.append(agent.user_skill_dir.parent / "agents")
    else:
        directories.append(Path.home() / "agents")
    directories.append(agent.root / "agents")
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            name = meta.get("name") or path.stem
            if name in {"explore", "plan", "general"}:
                continue
            allowed = None
            if meta.get("allowed-tools"):
                allowed = {item.strip() for item in meta["allowed-tools"].split(",") if item.strip()}
            found[name] = {
                "description": meta.get("description", ""),
                "system_prompt": body,
                "allowed_tools": allowed,
            }
    return found

def agent_types(agent) -> list[str]:
    names = ["explore", "plan", "general"]
    for agent_name in custom_agents(agent):
        if agent_name not in names:
            names.append(agent_name)
    return names

def run_child(
    agent,
    *,
    system_prompt: str,
    allowed_tools: set[str] | None,
    permission_mode: str,
    task: str,
    max_rounds: int,
    label: str,
) -> str:
    from .agent import Agent

    if agent.depth >= 1:
        return "权限拒绝：子 Agent 不能再创建子 Agent"
    child = Agent(
        root=agent.root,
        client=agent.client,
        permission_mode=permission_mode,
        max_rounds=max_rounds,
        enable_mcp=False,
        save_session=False,
        quiet=agent.quiet,
        depth=agent.depth + 1,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        user_skill_dir=agent.user_skill_dir,
    )
    agent.emit(f"[子 Agent {label}] 开始")
    try:
        answer = child.chat(task, augment_skills=False)
    finally:
        child.close()
    transcript = "\n".join(child.logs[-12:])
    return f"{answer}\n\n[子 Agent {label} 过程]\n{transcript}"

def run_subagent(agent, args: dict[str, Any]) -> str:
    agent_type = str(args.get("agent_type") or "general")
    task = str(args.get("task") or "")
    if not task:
        raise ValueError("缺少 task")
    custom = custom_agents(agent).get(agent_type)
    if agent_type in {"explore", "plan"} or agent.permission_mode == "plan":
        child_mode = "plan"
    else:
        child_mode = agent.permission_mode
    if agent_type == "explore":
        prompt, allowed = EXPLORE_PROMPT, set(READ_ONLY_TOOLS)
    elif agent_type == "plan":
        prompt, allowed = PLAN_PROMPT, set(READ_ONLY_TOOLS)
    elif custom:
        prompt = custom["system_prompt"] or GENERAL_PROMPT
        allowed = custom["allowed_tools"]
        if child_mode == "plan":
            allowed = set(READ_ONLY_TOOLS)
    elif agent_type == "general":
        prompt, allowed = GENERAL_PROMPT, None
    else:
        known = ", ".join(agent_types(agent))
        raise ValueError(f"未知子 Agent：{agent_type}。可选：{known}")
    return run_child(
        agent,
        system_prompt=prompt,
        allowed_tools=allowed,
        permission_mode=child_mode,
        task=task,
        max_rounds=min(6, agent.max_rounds),
        label=agent_type,
    )
