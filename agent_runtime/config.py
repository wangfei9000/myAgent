"""运行时常量和项目根目录。"""

from __future__ import annotations

from pathlib import Path

READ_ONLY_TOOLS = frozenset({"read_file", "list_files", "grep_search"})
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".idea",
    "my_knowledge_db",
}
SKIP_SUFFIXES = {".bin", ".sqlite3", ".pickle", ".pyc", ".png", ".jpg", ".jpeg", ".whl", ".zip"}
MAX_TOOL_CHARS = 6000
PERMISSION_MODES = ("plan", "default", "accept-edits")

#OPENAI_BASE_URL = "http://127.0.0.1:11434/v1"
#OPENAI_MODEL = "qwen3.5:9b"

#OPENAI_BASE_URL = "http://192.168.1.16:11434/v1"
#OPENAI_MODEL = "qwen3.8:27b"

OPENAI_BASE_URL = "https://api.deepseek.com"
OPENAI_API_KEY = "sk-5c9ecce2ae9846af9cb302baae22d54d"
OPENAI_MODEL = "deepseek-flash"


def script_dir() -> Path:
    """项目根目录。技能、MCP 配置和入口脚本都在这里。"""
    return Path(__file__).resolve().parent.parent
