"""把已发现的技能交给当前对话，或开一个只读子任务去执行。"""

from __future__ import annotations

import re
from typing import Any

from .config import READ_ONLY_TOOLS
from .skills import Skill, retrieve_skills
from .subagent import FORK_PROMPT, run_child

def skill_by_name(agent, name: str) -> Skill | None:
    for skill in agent.skills:
        if skill.name == name:
            return skill
    return None

def execute_skill(agent, args: dict[str, Any]) -> str:
    agent.refresh_skills()
    name = str(args.get("name") or "")
    skill = skill_by_name(agent, name)
    if skill is None:
        hits = retrieve_skills(agent.skills, name)
        if hits:
            skill = hits[0][0]
        else:
            names = ", ".join(item.name for item in agent.skills) or "无"
            return f"没有名为 {name} 的技能。当前技能：{names}"
    mode = str(args.get("mode") or "inline")
    if mode == "fork" and agent.depth >= 1:
        mode = "inline"
    if mode == "fork":
        task = str(args.get("task") or "按这个技能处理当前工作区里的相关问题")
        return run_child(
            agent,
            system_prompt=FORK_PROMPT + "\n\n" + skill.body,
            allowed_tools=set(READ_ONLY_TOOLS),
            permission_mode="plan",
            task=task,
            max_rounds=min(4, agent.max_rounds),
            label=f"skill:{skill.name}",
        )
    return (
        f"技能 {skill.name}（{skill.source}）已加载。请按下面的方法继续，不要把正文原样复述给用户。\n\n"
        + skill.body
    )

def create_skill(agent, args: dict[str, Any]) -> str:
    name = str(args.get("name") or "")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", name):
        raise ValueError("技能名只能使用小写字母、数字、下划线和连字符，且以字母开头")
    description = str(args.get("description") or "").replace("\n", " ")
    when = str(args.get("when_to_use") or "").replace("\n", " ")
    body = str(args.get("body") or "").strip()
    if not description or not body:
        raise ValueError("description 和 body 不能为空")
    path = agent.root / "skills" / name / "SKILL.md"
    if path.exists():
        raise FileExistsError(f"{name} 已存在。请先 read_file，再用 edit_file 修改")
    path.parent.mkdir(parents=True, exist_ok=True)
    when_line = f"when-to-use: {when}\n" if when else ""
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n{when_line}---\n\n{body}\n",
        encoding="utf-8",
    )
    agent.refresh_skills()
    return f"已创建技能 {name}：{path.relative_to(agent.root)}"
