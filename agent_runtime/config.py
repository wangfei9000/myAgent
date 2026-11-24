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

#OPENAI_BASE_URL = "https://api.deepseek.com"
#OPENAI_API_KEY = "sk-5c9ecce2ae9846af9cb302baae22d54d"
#OPENAI_MODEL = "deepseek-flash"

# 页面上的服务。接口、模型和密钥都写在这里，启动时用第一项。
PROVIDERS = (
    {
        "id": "deepseek",
        "label": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key": "sk-5c9ecce2ae9846af9cb302baae22d54d",
    },
    {
        "id": "ollama",
        "label": "ollama-qwen3.5:9b",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3.5:9b",
        "api_key": "123456",  # 只要不为空就行
    },
    {
        "id": "llama.cpp",
        "label": "llama.cpp-qwen3.5:9b",
        "base_url": "http://127.0.0.1:8081/v1",
        "model": "qwen3.5:9b",
        "api_key": "123456",  # 只要不为空就行
    },
    {
        "id": "qwen3.8:27bs",
        "label": "llama.cpp-qwen3.8-27bs",
        "base_url": "http://127.0.0.1:8082/v1",
        #"model": "qwen3.8:27b",
        "model":"Qwen3.5-4B-MLX-8bit",
        "api_key": "123456",  # 只要不为空就行
    },
)


def script_dir() -> Path:
    """项目根目录。技能、MCP 配置和入口脚本都在这里。"""
    return Path(__file__).resolve().parent.parent


def provider_by_id(provider_id: str | None) -> dict:
    """按 PROVIDERS 的 id 取服务。未指定时用第一项。"""
    if provider_id is None or not str(provider_id).strip():
        return PROVIDERS[0]
    wanted = str(provider_id).strip().lower()
    for item in PROVIDERS:
        if str(item["id"]).lower() == wanted:
            return item
    known = ", ".join(str(item["id"]) for item in PROVIDERS)
    raise ValueError(f"未知模型服务 {provider_id}。可选：{known}")
