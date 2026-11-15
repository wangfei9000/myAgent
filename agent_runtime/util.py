"""文本、路径和文件遍历的小工具。"""

from __future__ import annotations

import json
import os
import re
import ssl
from pathlib import Path
from typing import Any

from .config import MAX_TOOL_CHARS, SKIP_DIRS, SKIP_SUFFIXES

def clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[已截断 {len(text) - limit} 个字符]"


def asks_for_another_tool(content: str) -> bool:
    """模型只用文字预告下一步时，任务还没做完，不能把这句话当成最终答案。"""
    text = content.strip()
    if not text or len(text) > 280:
        return False
    markers = ("让我", "我将", "现在", "接下来", "然后点击", "需要登录", "输入用户名", "点击登录")
    return any(marker in text for marker in markers)


def https_context() -> ssl.SSLContext:
    """python.org 的 macOS 安装默认没有 CA 包，HTTPS 会报自签证书。改用系统证书。"""
    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca", 0) > 0:
        return context
    for candidate in (
        os.environ.get("SSL_CERT_FILE"),
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
    ):
        if candidate and os.path.isfile(candidate):
            context.load_verify_locations(candidate)
            break
    return context


def load_dotenv(root: Path) -> None:
    path = root / ".env"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, parts[2].strip()


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = re.findall(r"[a-z0-9_]+", lowered)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def is_secret_path(path: Path) -> bool:
    name = path.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name in {"credentials.json", "id_rsa", "id_rsa.pub"}:
        return True
    return path.suffix.lower() in {".pem", ".key"}


def parse_arguments(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        if isinstance(value, dict):
            return value
        return {"value": value}
    return {}


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            yield path
