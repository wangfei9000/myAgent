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
from urllib.parse import parse_qs, quote, unquote, urlparse

from agent_runtime.agent import create_agent
from agent_runtime.browser import archive_previous_run
from agent_runtime.config import PERMISSION_MODES, PROVIDERS, SKIP_DIRS, script_dir
from agent_runtime.util import is_secret_path, load_dotenv

WEB_DIR = script_dir() / "web"
SETTINGS_PATH = script_dir() / "web_settings.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
FILE_BYTES_LIMIT = 20_000_000
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
    parser.add_argument(
        "--model",
        default=None,
        help="按 config.py 里 PROVIDERS 的 id 选择服务，例如 deepseek、ollama、llama.cpp；默认第一项",
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--permission", choices=PERMISSION_MODES, default="default")
    parser.add_argument("--max-rounds", type=int, default=990)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--root", default=str(script_dir() / "exports"), help="工作区目录")
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


def load_web_settings() -> dict:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def public_settings(agent) -> dict:
    timeout = getattr(agent.client, "timeout", 300)
    return {
        "provider": current_provider(agent),
        "providers": listed_providers(),
        "permission": agent.permission_mode,
        "permissions": list(PERMISSION_MODES),
        "max_rounds": agent.max_rounds,
        "timeout": int(timeout),
    }


def apply_web_settings(agent, body: dict, *, persist: bool) -> str | None:
    provider = str(body.get("provider") or "").strip().lower()
    permission = str(body.get("permission") or "").strip()
    try:
        max_rounds = int(body.get("max_rounds"))
        timeout = int(body.get("timeout"))
    except (TypeError, ValueError):
        return "轮次和超时需要是整数"
    if not provider:
        return "请选择模型服务"
    if permission not in PERMISSION_MODES:
        return "未知权限模式"
    if max_rounds < 1 or max_rounds > 1000:
        return "轮次需要在 1 到 1000 之间"
    if timeout < 1 or timeout > 3600:
        return "超时需要在 1 到 3600 秒之间"
    provider_error = use_provider(agent, provider)
    if provider_error:
        return provider_error
    agent.permission_mode = permission
    agent.max_rounds = max_rounds
    if hasattr(agent.client, "timeout"):
        agent.client.timeout = float(timeout)
    if persist:
        payload = {
            "provider": provider,
            "permission": permission,
            "max_rounds": max_rounds,
            "timeout": timeout,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        temporary = SETTINGS_PATH.with_suffix(".json.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(SETTINGS_PATH)
        except OSError:
            return "设置文件无法写入"
    return None


def workspace_path(agent, raw: str) -> Path:
    root = agent.root.resolve()
    text = unquote(str(raw or "")).strip().replace("\\", "/")
    if text.startswith("/") or ".." in Path(text).parts:
        raise PermissionError("路径不可访问")
    parts = [part for part in Path(text).parts if part not in ("", ".")]
    if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
        raise PermissionError("路径不可访问")
    target = root.joinpath(*parts).resolve() if parts else root
    if target != root and root not in target.parents:
        raise PermissionError("路径不可访问")
    if is_secret_path(target):
        raise PermissionError("路径不可访问")
    return target


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".doc", ".docx", ".dotx"}:
        return "doc"
    if suffix in {".xls", ".xlsx", ".xlsm", ".csv", ".tsv"}:
        return "sheet"
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".html", ".css", ".md"}:
        return "code" if suffix != ".md" else "text"
    if suffix == ".sql":
        return "sql"
    if suffix in {".txt", ".log"}:
        return "text"
    if suffix in {".zip", ".gz", ".tgz", ".tar"}:
        return "archive"
    return "file"


def list_workspace(agent, raw: str) -> dict:
    root = agent.root.resolve()
    target = workspace_path(agent, raw)
    if not target.is_dir():
        raise FileNotFoundError("没有这个目录")
    relative = "" if target == root else target.relative_to(root).as_posix()
    parent = None
    if target != root:
        parent = "" if target.parent == root else target.parent.relative_to(root).as_posix()
    items = []
    for child in target.iterdir():
        if child.name.startswith(".") or child.name in SKIP_DIRS or is_secret_path(child):
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        is_dir = child.is_dir()
        items.append(
            {
                "name": child.name,
                "path": child.relative_to(root).as_posix(),
                "kind": "dir" if is_dir else file_kind(child),
                "size": None if is_dir else stat.st_size,
            }
        )
    items.sort(key=lambda item: (item["kind"] != "dir", item["name"].lower()))
    return {"path": relative, "parent": parent, "root_name": root.name, "items": items}


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
        if path == "/api/settings":
            self._json(200, public_settings(agent))
            return
        if path == "/api/files":
            self._files(agent)
            return
        if path == "/api/files/content":
            self._file_content(agent)
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
            message += (
                "\n\n只查询时把结果写在回答里。"
                "用户要求 Excel、PDF 或 Word 时，按已加载的 xlsx、pdf、docx 技能生成文件，"
                "并且直接写在当前工作区。"
            )
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
        if path == "/api/settings":
            global STATE_VERSION
            with STATE_CHANGED:
                if STATE["running"]:
                    self._json(409, {"error": "任务执行中，不能修改设置"})
                    return
                error = apply_web_settings(agent, body, persist=True)
                if error:
                    self._json(400, {"error": error})
                    return
                STATE_VERSION += 1
                STATE_CHANGED.notify_all()
            self._json(200, public_settings(agent))
            return
        if path == "/api/reset":
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

    def _query_path(self) -> str:
        values = parse_qs(urlparse(self.path).query)
        return values.get("path", [""])[0]

    def _files(self, agent) -> None:
        try:
            payload = list_workspace(agent, self._query_path())
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
            return
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
            return
        self._json(200, payload)

    def _file_content(self, agent) -> None:
        try:
            path = workspace_path(agent, self._query_path())
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
            return
        if not path.is_file():
            self._json(404, {"error": "没有这个文件"})
            return
        try:
            size = path.stat().st_size
        except OSError:
            self._json(404, {"error": "没有这个文件"})
            return
        if size > FILE_BYTES_LIMIT:
            self._json(413, {"error": "文件太大，不能在页面里打开"})
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        inline = path.suffix.lower() in IMAGE_SUFFIXES
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        disposition = "inline" if inline else "attachment"
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(path.name)}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

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
        if content_type.startswith("text/") or content_type.startswith("application/javascript"):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd())
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive_previous_run(root)
    try:
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
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    Handler.agent = agent
    client = agent.client
    STATE["origin"] = {
        "model": getattr(client, "model", ""),
        "base_url": getattr(client, "base_url", ""),
        "api_key": getattr(client, "api_key", ""),
    }
    saved = load_web_settings()
    if saved:
        error = apply_web_settings(agent, saved, persist=False)
        if error:
            print(f"忽略 web_settings.json：{error}")
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
