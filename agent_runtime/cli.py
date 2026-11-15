"""命令行参数、交互 REPL 和启动。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import Agent, create_agent
from .browser import archive_previous_run
from .config import PERMISSION_MODES, script_dir
from .util import load_dotenv

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="参考 MyAgent 的 Agent Runtime")
    parser.add_argument("prompt", nargs="*", help="一次性任务；省略则进入 REPL")
    parser.add_argument("--prompt", dest="prompt_file", help="从文件读取一次性任务提示词")
    parser.add_argument("--model", default=None, help="模型名，默认 MODEL 或 qwen3:8b")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible 根地址，默认本机 Ollama /v1")
    parser.add_argument("--api-key", default=None, help="API key，Ollama 可省略")
    parser.add_argument("--permission", choices=PERMISSION_MODES, default="default")
    parser.add_argument("--plan", action="store_true", help="只读计划模式")
    parser.add_argument("--accept-edits", action="store_true", help="允许工作区内改文件，并允许受限制的 Shell")
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=300, help="单次模型请求最长等待秒数，默认 300")
    parser.add_argument(
        "--log-model",
        action="store_true",
        help="把每次发给模型的文字和模型返回的文字写入 logs/model.log，默认关闭",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="工作区目录。默认是项目根目录；技能和 .mcp.json 也从该目录读取",
    )
    parser.add_argument("--skill", default=None, help="按名称执行一个 SKILL.md，例如 project_brief")
    parser.add_argument("--resume", action="store_true", help="恢复 sessions/latest.json")
    parser.add_argument("--no-mcp", action="store_true")
    return parser


def resolve_mode(args: argparse.Namespace) -> str:
    if args.plan and args.accept_edits:
        raise SystemExit("不能同时指定 --plan 和 --accept-edits")
    if args.plan:
        return "plan"
    if args.accept_edits:
        return "accept-edits"
    return args.permission


def run_repl(agent: Agent) -> None:
    print("输入任务开始。/skill 名称 [任务]  /plan  /default  /accept-edits  /skills  /tools  /compact  /quit")
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            return
        if line == "/plan":
            agent.permission_mode = "plan"
            print("权限已切换为 plan")
            continue
        if line == "/default":
            agent.permission_mode = "default"
            print("权限已切换为 default")
            continue
        if line == "/accept-edits":
            agent.permission_mode = "accept-edits"
            print("权限已切换为 accept-edits")
            continue
        if line == "/skills":
            agent.refresh_skills()
            if not agent.skills:
                print("还没有技能。")
            for skill in agent.skills:
                print(f"- {skill.name} [{skill.source}] {skill.description}")
            continue
        if line.startswith("/skill"):
            parts = line.split(maxsplit=2)
            if len(parts) < 2:
                print("用法：/skill 名称 [任务]")
                continue
            task = parts[2] if len(parts) > 2 else f"按 {parts[1]} 处理当前工作区"
            try:
                print(agent.chat(task, skill=parts[1]))
            except Exception as exc:
                print(f"请求失败：{exc}")
            continue
        if line == "/tools":
            print("\n".join(tool.name for tool in agent.visible_tools()))
            continue
        if line == "/compact":
            print(agent.compact())
            continue
        try:
            print(agent.chat(line))
        except Exception as exc:
            print(f"请求失败：{exc}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_dotenv(Path.cwd())
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.prompt_file and args.prompt:
        parser.error("不能同时指定位置提示词和 --prompt 文件")
    mode = resolve_mode(args)
    root = Path(args.root or script_dir()).resolve()
    archived = archive_previous_run(root)
    agent = create_agent(
        root=root,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        permission_mode=mode,
        max_rounds=args.max_rounds,
        timeout=args.timeout,
        enable_mcp=not args.no_mcp,
        log_model=args.log_model,
    )
    try:
        model_name = getattr(agent.client, "model", "scripted")
        base_url = getattr(agent.client, "base_url", "")
        agent.refresh_skills()
        skill_names = ", ".join(item.name for item in agent.skills) or "无"
        print(f"模型 {model_name}  接口 {base_url}  权限 {mode}  工作区 {agent.root}")
        print(f"技能 {skill_names}")
        if archived:
            print(f"上一份模型日志 {archived.name}，截图 screenshots/{archived.stem}/")
        if args.log_model:
            print(f"模型日志 {agent.root / 'logs' / 'model.log'}")
            print(f"截图目录 {agent.root / 'screenshots' / 'model'}")
        if args.resume:
            print(agent.load_session())
        prompt = " ".join(args.prompt).strip()
        if args.prompt_file:
            prompt_path = Path(args.prompt_file).expanduser()
            if not prompt_path.is_absolute():
                prompt_path = Path.cwd() / prompt_path
            try:
                prompt = prompt_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(f"无法读取提示词文件 {prompt_path}: {exc}") from exc
            if not prompt:
                raise ValueError(f"提示词文件为空：{prompt_path}")
        if args.skill and not prompt:
            prompt = f"按 {args.skill} 处理当前工作区"
        if prompt:
            print(agent.chat(prompt, skill=args.skill))
            return 0
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
            if not text:
                parser.print_help()
                return 2
            print(agent.chat(text, skill=args.skill))
            return 0
        run_repl(agent)
        return 0
    except Exception as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 1
    finally:
        agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
