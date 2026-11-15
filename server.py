#!/usr/bin/env python3
"""HTTP 入口。

  python3 server.py
  python3 server.py --port 8080

页面在 web/index.html。任务接口会立刻返回，进度用 GET /api/run 查看。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from agent_runtime.agent import create_agent
from agent_runtime.browser import archive_previous_run
from agent_runtime.config import PERMISSION_MODES, PROVIDERS, script_dir
from agent_runtime.util import load_dotenv

WEB_DIR = script_dir() / "web"
CHAT_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
STATE_CHANGED = threading.Condition(STATE_LOCK)
STATE_VERSION = 0
STATE = {
    "running": False,
    "started": False,
    "log_from": 0,
    "started_at": 0.0,
    "reply": "",
    "error": "",
    "origin": {},
}
SHOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.png$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--permission", choices=PERMISSION_MODES, default="default")
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--root", default=None, help="工作区目录，默认是项目根目录")
    parser.add_argument("--no-mcp", action="store_true")
    parser.add_argument("--log-model", action="store_true")
    return parser


def read_body(handler: BaseHTTPRequestHandler) -> dict | None:
    length = int(handler.headers.get("Content-Length") or "0")
    if length > 1_000_000:
        return None
    try:
        raw = handler.rfile.read(length).decode("utf-8")
        body = json.loads(raw or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def screenshot_items(agent, since: float) -> list[dict[str, str]]:
    directory = agent.root / "screenshots" / "model"
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.png")):
        if not SHOT_NAME.match(path.name):
            continue
        if path.stat().st_mtime < since:
            continue
        items.append({"name": path.name, "url": "/screenshots/" + path.name})
    return items


def public_run(agent) -> dict:
    with STATE_LOCK:
        running = STATE["running"]
        started = STATE["started"]
        log_from = STATE["log_from"]
        started_at = STATE["started_at"]
        reply = STATE["reply"]
        error = STATE["error"]
    logs = list(agent.logs[log_from:]) if started else []
    shots = screenshot_items(agent, started_at) if started else []
    return {
        "running": running,
        "logs": logs,
        "reply": reply,
        "error": error,
        "screenshots": shots,
        "history": len(agent.history),
    }


def match_provider(base_url: str, model: str) -> str:
    text = f"{base_url} {model}".lower()
    if "11434" in text or "ollama" in text or "qwen" in text:
        return "ollama"
    return "deepseek"


def current_provider(agent) -> str:
    client = agent.client
    return match_provider(
        str(getattr(client, "base_url", "") or ""),
        str(getattr(client, "model", "") or ""),
    )


def listed_providers() -> list[dict[str, str]]:
    return [{"id": item["id"], "label": item["label"]} for item in PROVIDERS]


def use_provider(agent, provider_id: str | None) -> str | None:
    if provider_id is None:
        return None
    provider_id = str(provider_id).strip().lower()
    if not provider_id:
        return None
    presets = {item["id"]: item for item in PROVIDERS}
    if provider_id not in presets:
        return "未知服务"
    client = agent.client
    if not hasattr(client, "model"):
        return "当前运行时不能切换服务"
    origin = STATE.get("origin") or {}
    origin_id = match_provider(str(origin.get("base_url") or ""), str(origin.get("model") or ""))
    if provider_id == origin_id and origin.get("model"):
        client.model = origin["model"]
        if origin.get("base_url") and hasattr(client, "base_url"):
            client.base_url = origin["base_url"]
        if "api_key" in origin and hasattr(client, "api_key"):
            client.api_key = origin["api_key"]
        return None
    preset = presets[provider_id]
    client.model = preset["model"]
    if hasattr(client, "base_url"):
        client.base_url = str(preset["base_url"]).rstrip("/")
    if hasattr(client, "api_key"):
        client.api_key = preset.get("api_key", "")
    return None


def start_chat(
    agent,
    message: str,
    skill: str | None,
    permission: str | None,
    max_rounds: int | None,
    provider: str | None = None,
) -> str | None:
    if permission is not None and permission not in PERMISSION_MODES:
        return "未知权限模式"
    if max_rounds is not None and (max_rounds < 1 or max_rounds > 1000):
        return "轮次需要在 1 到 1000 之间"
    global STATE_VERSION
    with STATE_CHANGED:
        if STATE["running"]:
            return "已有任务在执行"
        provider_error = use_provider(agent, provider)
        if provider_error:
            return provider_error
        if permission is not None:
            agent.permission_mode = permission
        if max_rounds is not None:
            agent.max_rounds = max_rounds
        STATE["running"] = True
        STATE["started"] = True
        STATE["log_from"] = len(agent.logs)
        STATE["started_at"] = time.time()
        STATE["reply"] = ""
        STATE["error"] = ""
        STATE_VERSION += 1
        STATE_CHANGED.notify_all()

    def work() -> None:
        global STATE_VERSION
        try:
            with CHAT_LOCK:
                reply = agent.chat(message, skill=skill)
            with STATE_CHANGED:
                STATE["reply"] = reply
                STATE_VERSION += 1
                STATE_CHANGED.notify_all()
        except Exception as exc:
            with STATE_CHANGED:
                STATE["error"] = str(exc)
                STATE_VERSION += 1
                STATE_CHANGED.notify_all()
        finally:
            with STATE_CHANGED:
                STATE["running"] = False
                STATE_VERSION += 1
                STATE_CHANGED.notify_all()

    threading.Thread(target=work, daemon=True).start()
    return None


class Handler(BaseHTTPRequestHandler):
    agent = None

    def do_GET(self) -> None:
        agent = self.agent
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(200, {"ok": True})
            return
        if path == "/api/status":
            client = agent.client
            self._json(
                200,
                {
                    "model": getattr(client, "model", ""),
                    "provider": current_provider(agent),
                    "providers": listed_providers(),
                    "permission": agent.permission_mode,
                    "max_rounds": agent.max_rounds,
                    "running": STATE["running"],
                },
            )
            return
        if path == "/api/skills":
            agent.refresh_skills()
            self._json(
                200,
                {
                    "skills": [
                        {
                            "name": skill.name,
                            "description": skill.description,
                            "when": skill.when_to_use,
                        }
                        for skill in agent.skills
                    ]
                },
            )
            return
        if path == "/api/run":
            self._json(200, public_run(agent))
            return
        if path == "/api/run/stream":
            self._run_stream(agent)
            return
        if path.startswith("/screenshots/"):
            self._screenshot(path.removeprefix("/screenshots/"))
            return
        if path.startswith("/api/"):
            self._json(404, {"error": "没有这个接口"})
            return
        self._file(path)

    def do_POST(self) -> None:
        agent = self.agent
        path = urlparse(self.path).path
        body = read_body(self)
        if body is None:
            self._json(400, {"error": "请求体不是 JSON"})
            return
        if path == "/api/chat":
            message = str(body.get("message") or "").strip()
            if not message:
                self._json(400, {"error": "缺少 message"})
                return
            skill = str(body.get("skill") or "").strip() or None
            provider = body.get("provider")
            provider = str(provider) if provider else None
            permission = body.get("permission")
            permission = str(permission) if permission else None
            rounds = body.get("max_rounds")
            max_rounds = None
            if rounds not in (None, ""):
                try:
                    max_rounds = int(rounds)
                except (TypeError, ValueError):
                    self._json(400, {"error": "轮次不是整数"})
                    return
            error = start_chat(agent, message, skill, permission, max_rounds, provider)
            if error == "已有任务在执行":
                self._json(409, {"error": error})
                return
            if error:
                self._json(400, {"error": error})
                return
            self._json(202, {"ok": True})
            return
        if path == "/api/reset":
            global STATE_VERSION
            with STATE_CHANGED:
                if STATE["running"]:
                    self._json(409, {"error": "任务执行中，不能清空"})
                    return
                agent.history.clear()
                STATE["started"] = False
                STATE["reply"] = ""
                STATE["error"] = ""
                STATE["log_from"] = len(agent.logs)
                STATE_VERSION += 1
                STATE_CHANGED.notify_all()
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "没有这个接口"})

    def _screenshot(self, name: str) -> None:
        name = unquote(name)
        if not SHOT_NAME.match(name):
            self._json(404, {"error": "没有这张截图"})
            return
        path = (self.agent.root / "screenshots" / "model" / name).resolve()
        root = (self.agent.root / "screenshots" / "model").resolve()
        if root not in path.parents or not path.is_file():
            self._json(404, {"error": "没有这张截图"})
            return
        self._bytes(200, "image/png", path.read_bytes())

    def _run_stream(self, agent) -> None:
        """通过 SSE 推送任务状态，避免浏览器反复请求 /api/run。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_signature = None
        last_heartbeat = time.monotonic()
        try:
            while True:
                payload = public_run(agent)
                signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                now = time.monotonic()
                if signature != last_signature:
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"event: run\n")
                    self.wfile.write(b"data: " + data + b"\n\n")
                    self.wfile.flush()
                    last_signature = signature
                    last_heartbeat = now
                elif now - last_heartbeat >= 15:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    last_heartbeat = now

                with STATE_CHANGED:
                    STATE_CHANGED.wait(timeout=0.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _file(self, url_path: str) -> None:
        rel = unquote(url_path).lstrip("/") or "index.html"
        root = WEB_DIR.resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            self._json(403, {"error": "路径越界"})
            return
        if target.is_file():
            data = target.read_bytes()
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".html":
                mime = "text/html; charset=utf-8"
            self._bytes(200, mime, data)
            return
        self._json(404, {"error": "没有这个页面"})

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(status, "application/json; charset=utf-8", data)

    def _bytes(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd())
    args = build_parser().parse_args(argv)
    root = Path(args.root or script_dir()).resolve()
    archive_previous_run(root)
    agent = create_agent(
        root=root,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        permission_mode=args.permission,
        max_rounds=args.max_rounds,
        timeout=args.timeout,
        enable_mcp=not args.no_mcp,
        log_model=args.log_model,
    )
    Handler.agent = agent
    client = agent.client
    STATE["origin"] = {
        "model": getattr(client, "model", ""),
        "base_url": getattr(client, "base_url", ""),
        "api_key": getattr(client, "api_key", ""),
    }
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"http://{args.host}:{args.port}  工作区 {agent.root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
