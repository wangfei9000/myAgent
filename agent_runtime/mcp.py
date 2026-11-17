"""MCP stdio 连接和工具路由。"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from .config import script_dir

def read_mcp_message(stream) -> dict[str, Any] | None:
    """读取一条 MCP stdio 消息。官方帧是 Content-Length，也兼容一行一个 JSON。"""
    first = stream.read(1)
    if not first:
        return None
    if first == b"{":
        rest = stream.readline()
        if rest == b"" and stream.closed:
            return None
        return json.loads((first + rest).decode("utf-8"))

    header = first + stream.readline()
    lines = [header]
    while True:
        line = stream.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        lines.append(line)
    length = 0
    for line in b"".join(lines).decode("ascii", "replace").splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            raise EOFError("MCP 消息不完整")
        body += chunk
    return json.loads(body.decode("utf-8"))


def write_mcp_message(stream, payload: dict[str, Any]) -> None:
    # 新的 MCP stdio 服务器按一行一个 JSON 读取。Content-Length 帧没有换行时，
    # 对方会一直阻塞，initialize 表现为超时。
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


def ensure_local_playwright(cwd: Path) -> None:
    """本地没有 cli.js 时安装一次。之后用 node 直接启动，不再走 npx。"""
    if (cwd / "node_modules" / "@playwright" / "mcp" / "cli.js").is_file():
        return
    subprocess.run(
        ["npm", "install", "--prefix", str(cwd), "@playwright/mcp@0.0.85"],
        check=True,
    )


class McpConnection:
    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str], cwd: Path):
        self.name = name
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.pending: dict[int, queue.Queue] = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.next_id = 1
        self.stderr_tail = b""
        self._closed = False

    def connect(self) -> None:
        merged = os.environ.copy()
        merged.update(self.env)
        merged["PYTHONUNBUFFERED"] = "1"
        if self.args[:1] == ["node_modules/@playwright/mcp/cli.js"]:
            ensure_local_playwright(self.cwd)
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.cwd),
            env=merged,
            start_new_session=True,
        )
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        chunks: list[bytes] = []
        for line in self.proc.stderr:
            chunks.append(line)
            self.stderr_tail = b"".join(chunks)[-2000:]

    def _fail_all(self, message: str) -> None:
        with self.lock:
            pending = list(self.pending.items())
            self.pending.clear()
        for _, mailbox in pending:
            try:
                mailbox.put_nowait(("error", message))
            except queue.Full:
                pass

    def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while not self._closed:
                message = read_mcp_message(self.proc.stdout)
                if message is None:
                    break
                self._deliver(message)
        except Exception as exc:
            self._fail_all(f"MCP 读取失败：{exc}")
            return
        self._fail_all(f"MCP 服务 {self.name} 已关闭")

    def _deliver(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        if msg_id is None:
            return
        with self.lock:
            mailbox = self.pending.pop(msg_id, None)
        if mailbox is None:
            return
        if "error" in message:
            error = message["error"] or {}
            mailbox.put(("error", f"{error.get('code')}: {error.get('message')}"))
        else:
            mailbox.put(("ok", message.get("result")))

    def request(self, method: str, params: dict | None = None, timeout: float = 20) -> Any:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError(f"MCP 服务 {self.name} 没有启动")
        with self.lock:
            req_id = self.next_id
            self.next_id += 1
            mailbox: queue.Queue = queue.Queue(maxsize=1)
            self.pending[req_id] = mailbox
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        with self.write_lock:
            write_mcp_message(self.proc.stdin, payload)
        try:
            kind, value = mailbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"MCP 服务 {self.name} 调用 {method} 超时") from exc
        if kind == "error":
            detail = self.stderr_tail.decode("utf-8", "replace").strip()
            suffix = f"；stderr: {detail}" if detail else ""
            raise RuntimeError(f"{value}{suffix}")
        return value

    def notify(self, method: str, params: dict | None = None) -> None:
        if not self.proc or not self.proc.stdin:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self.write_lock:
            write_mcp_message(self.proc.stdin, payload)

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "my-agent", "version": "0.1.0"},
            },
            timeout=190,
        )
        self.notify("notifications/initialized")

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", timeout=90)
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=60)
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            texts = [
                str(item.get("text", ""))
                for item in result["content"]
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if texts:
                return "\n".join(texts)
        return json.dumps(result, ensure_ascii=False)

    def close(self) -> None:
        self._closed = True
        proc = self.proc
        self.proc = None
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        self._fail_all(f"MCP 服务 {self.name} 已关闭")


# Playwright 会暴露二十多个工具。全部塞进请求会超过 qwen3.5 的 4096 上下文，
# CPU 上会一直不出第一个字。只留下打开页面、查看和操作所需的那一组。
PLAYWRIGHT_TOOLS = {
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_press_key",
    "browser_wait_for",
    "browser_take_screenshot",
}
# 只给运行时等页面加载，不放进模型的工具列表。
PLAYWRIGHT_INTERNAL_TOOLS = {
    "browser_evaluate",
}
PLAYWRIGHT_RESULT_CHARS = 16000


class McpManager:
    def __init__(self, root: Path):
        self.root = root
        self.connections: dict[str, McpConnection] = {}
        self.routes: dict[str, tuple[str, str]] = {}
        self.definitions: list[dict[str, Any]] = []
        self.connected = False

    def load_and_connect(self) -> list[str]:
        if self.connected:
            return []
        self.connected = True
        notes = self.connect_configs(self._load_configs())
        return notes

    def connect_configs(self, configs: dict[str, dict]) -> list[str]:
        notes: list[str] = []
        for name, config in configs.items():
            if not isinstance(config, dict) or "command" not in config:
                continue
            connection = McpConnection(
                name,
                str(config["command"]),
                [str(item) for item in config.get("args") or []],
                {str(k): str(v) for k, v in (config.get("env") or {}).items()},
                script_dir(),
            )
            try:
                connection.connect()
                connection.initialize()
                tools = connection.list_tools()
            except Exception as exc:
                connection.close()
                notes.append(f"MCP 连接失败 {name}：{exc}")
                continue
            self.connections[name] = connection
            kept = 0
            for tool in tools:
                tool_name = str(tool.get("name", "tool"))
                visible = not (name == "playwright" and tool_name not in PLAYWRIGHT_TOOLS)
                internal = name == "playwright" and tool_name in PLAYWRIGHT_INTERNAL_TOOLS
                if not visible and not internal:
                    continue
                exposed = self._expose_name(name, tool_name)
                self.routes[exposed] = (name, tool_name)
                if not visible:
                    continue
                description = tool.get("description") or f"{name} 的 MCP 工具 {tool_name}"
                self.definitions.append(
                    {
                        "name": exposed,
                        "description": description.strip().splitlines()[0][:240],
                        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                    }
                )
                kept += 1
            notes.append(f"MCP 已连接 {name}（{kept} 个工具）")
        return notes

    def _expose_name(self, server: str, tool: str) -> str:
        raw = re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{tool}")
        name = raw
        suffix = 2
        while name in self.routes:
            name = f"{raw}_{suffix}"
            suffix += 1
        return name

    def call(self, exposed_name: str, arguments: dict) -> str:
        server_name, tool_name = self.routes[exposed_name]
        connection = self.connections.get(server_name)
        if connection is None:
            raise RuntimeError(f"MCP 服务 {server_name} 未连接")
        return connection.call_tool(tool_name, arguments)

    def close(self) -> None:
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()

    def _load_configs(self) -> dict[str, dict]:
        merged: dict[str, dict] = {}
        paths = [script_dir() / "settings.json", script_dir() / ".mcp.json"]
        paths.extend((self.root / "settings.json", self.root / ".mcp.json"))
        seen: set[Path] = set()
        for path in paths:
            resolved = path.resolve() if path.exists() else path
            if resolved in seen:
                continue
            seen.add(resolved)
            self._merge_file(path, merged)
        return merged

    @staticmethod
    def _merge_file(path: Path, target: dict[str, dict]) -> None:
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        servers = raw.get("mcpServers", raw) if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            return
        for name, config in servers.items():
            if isinstance(config, dict) and "command" in config:
                target[str(name)] = config
