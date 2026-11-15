"""工作区路径沙箱，以及读、写、搜索和受限制的 Shell。"""

from __future__ import annotations

import re
import shlex
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .util import is_secret_path, iter_files

def resolve_inside(agent, raw: str) -> Path:
    if not str(raw).strip():
        raise ValueError("缺少路径")
    path = Path(raw)
    if not path.is_absolute():
        path = agent.root / path
    resolved = path.resolve()
    root = agent.root.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError(f"路径越出工作区：{resolved}")
    if is_secret_path(resolved):
        raise PermissionError(f"拒绝访问密钥文件：{resolved.name}")
    return resolved

def authorize_shell(command: str) -> str | None:
    if not command.strip():
        return "工具参数错误：缺少 command"
    if re.search(r"[|&;<>`\n]|\$\(", command):
        return "权限拒绝：只允许单条命令，不能使用管道、重定向或命令替换"
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"工具参数错误：{exc}"
    if not argv:
        return "工具参数错误：缺少 command"
    program = Path(argv[0]).name
    if program in {"bash", "sh", "zsh", "dash"} and "-c" in argv:
        return "权限拒绝：不能通过 shell -c 绕过命令检查"
    denied = {
        "sudo",
        "rm",
        "mkfs",
        "dd",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "kill",
        "pkill",
        "killall",
        "diskutil",
    }
    if program in denied:
        return f"权限拒绝：禁止执行 {program}"
    return None

def read_file(agent, args: dict[str, Any]) -> str:
    path = resolve_inside(agent, str(args.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    offset = max(int(args.get("offset") or 1), 1)
    limit = int(args.get("limit") or 200)
    start = offset - 1
    chunk = lines[start : start + limit]
    agent.read_state[str(path)] = path.stat().st_mtime_ns
    if not chunk:
        return f"{path.name} 在行 {offset} 之后没有内容，文件共 {len(lines)} 行。"
    numbered = [f"{start + index + 1}|{line}" for index, line in enumerate(chunk)]
    header = f"{path.relative_to(agent.root)} 共 {len(lines)} 行，显示 {offset}-{offset + len(chunk) - 1}"
    return header + "\n" + "\n".join(numbered)

def write_file(agent, args: dict[str, Any]) -> str:
    path = resolve_inside(agent, str(args.get("path", "")))
    if "content" not in args:
        raise ValueError("缺少 content")
    if path.exists():
        seen = agent.read_state.get(str(path))
        if seen is None:
            raise PermissionError("覆盖已有文件前必须先 read_file")
        if seen != path.stat().st_mtime_ns:
            raise PermissionError("文件在读取后已变化，请重新 read_file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(args["content"]), encoding="utf-8")
    # 写入不算已读。覆盖后的 mtime 也会失效，下一次 edit 必须重新 read_file。
    agent.read_state.pop(str(path), None)
    return f"已写入 {path.relative_to(agent.root)}"

def edit_file(agent, args: dict[str, Any]) -> str:
    path = resolve_inside(agent, str(args.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError(path)
    seen = agent.read_state.get(str(path))
    if seen is None:
        raise PermissionError("编辑前必须先 read_file")
    if seen != path.stat().st_mtime_ns:
        raise PermissionError("文件在读取后已变化，请重新 read_file")
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    if not old:
        raise ValueError("缺少 old_string")
    if old == new:
        raise ValueError("原文和新文相同")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ValueError("没有找到要替换的原文")
    if count > 1:
        raise ValueError(f"原文匹配了 {count} 处，请给出更长的片段")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    agent.read_state[str(path)] = path.stat().st_mtime_ns
    return f"已更新 {path.relative_to(agent.root)}"

def list_files(agent, args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern") or "*")
    matches = []
    for path in iter_files(agent.root):
        relative = path.relative_to(agent.root).as_posix()
        if fnmatch(relative, pattern) or fnmatch(path.name, pattern):
            matches.append(relative)
        if len(matches) >= 200:
            break
    if not matches:
        return "没有匹配文件"
    suffix = "\n...[已达到 200 条上限]" if len(matches) >= 200 else ""
    return "\n".join(matches) + suffix

def grep_search(agent, args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise ValueError("缺少 pattern")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"无效正则：{exc}") from exc
    glob_pattern = str(args.get("glob") or "*")
    hits = []
    for path in iter_files(agent.root):
        relative = path.relative_to(agent.root).as_posix()
        if glob_pattern != "*" and not (
            fnmatch(relative, glob_pattern) or fnmatch(path.name, glob_pattern)
        ):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(lines, 1):
            if regex.search(line):
                hits.append(f"{relative}:{line_number}:{line[:200]}")
                if len(hits) >= 50:
                    return "\n".join(hits) + "\n...[已截断]"
    return "\n".join(hits) or "没有匹配"

def run_shell(agent, args: dict[str, Any]) -> str:
    command = str(args.get("command", ""))
    denied = authorize_shell(command)
    if denied:
        return denied
    argv = shlex.split(command)
    completed = subprocess.run(
        argv,
        cwd=agent.root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if not output:
        output = "（无输出）"
    return f"exit={completed.returncode}\n{output}"
