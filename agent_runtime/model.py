"""OpenAI-compatible /chat/completions 客户端。不依赖 openai 包。"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .util import https_context, parse_arguments

@dataclass
class ModelTurn:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class OpenAIClient:
    """OpenAI-compatible /chat/completions。不依赖 openai 包。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 300,
        log_path: Path | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.log_path = log_path

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        url = self.base_url + "/chat/completions"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        # qwen3.5 默认先写 <think>。Ollama 上 think:false 关不掉，要用 reasoning_effort=none，
        # 并改成流式，否则 CPU 上要等整段生成完才有第一个字节，容易读超时。
        # llama.cpp 开了 --jinja 时，会在每次请求里把思考拆到 reasoning_content，content 变成空。
        # chat_template_kwargs.enable_thinking=false 会让模板立刻闭合 <think>，正文回到 content。
        if self._ollama_endpoint():
            body["reasoning_effort"] = "none"
            body["stream"] = True
        elif "qwen3" in self.model.lower():
            body["chat_template_kwargs"] = {"enable_thinking": False}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        self._write_model_log(
            "发送",
            self._sent_text(messages, tools),
            context_chars=len(data.decode("utf-8")),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=https_context()) as response:
                if body["stream"]:
                    turn = self._read_stream(response)
                else:
                    payload = json.loads(response.read().decode("utf-8"))
                    turn = self._turn_from_message(payload["choices"][0]["message"])
        except TimeoutError as exc:
            error = RuntimeError(
                f"模型在 {self.timeout:.0f} 秒内没有新输出。qwen3.5 跑在 CPU 上时，"
                "工具说明太长会让第一轮一直不出字。浏览器工具已缩小到打开页面和查看页面；"
                "若仍然超时，启动时加上 --timeout 600。"
            )
            self._write_model_log("返回", str(error))
            raise error from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            error = RuntimeError(f"模型接口返回 {exc.code}：{detail}")
            self._write_model_log("返回", str(error))
            raise error from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                error = RuntimeError(f"无法校验模型接口 {self.base_url} 的证书：{exc.reason}")
            else:
                error = RuntimeError(
                    f"无法连接模型接口 {self.base_url}：{exc.reason}。"
                    "若使用本机 Ollama，请先执行 ollama serve，并确认模型支持工具调用。"
                )
            self._write_model_log("返回", str(error))
            raise error from exc
        self._write_model_log("返回", self._returned_text(turn))
        return turn

    def _turn_from_message(self, message: dict[str, Any]) -> ModelTurn:
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            calls.append(
                {
                    "id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "name": function.get("name") or "",
                    "arguments": parse_arguments(function.get("arguments")),
                }
            )
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not str(content).strip() and not calls:
            content = message.get("reasoning_content") or message.get("reasoning") or ""
        return ModelTurn(str(content), calls)

    def _sent_text(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
        names = [
            str((tool.get("function") or {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        ]
        lines = [f"附带工具：{', '.join(name for name in names if name) or '无'}"]
        for message in messages:
            lines.append(self._message_text(message))
        return "\n\n".join(lines)

    def _returned_text(self, turn: ModelTurn) -> str:
        lines = [turn.content or "（无正文）"]
        for call in turn.tool_calls:
            lines.append(
                f"工具 {call.get('name', '')} {json.dumps(call.get('arguments') or {}, ensure_ascii=False)}"
            )
        return "\n".join(lines)

    def _message_text(self, message: dict[str, Any]) -> str:
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        lines = [f"[{message.get('role', '')}]", str(content)]
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            lines.append(f"工具 {function.get('name', '')} {function.get('arguments', '')}")
        return "\n".join(lines)

    def _write_model_log(self, kind: str, text: str, *, context_chars: int | None = None) -> None:
        if self.log_path is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"===== {stamp} {kind}"
        if context_chars is not None:
            title += f" 上下文长度 {context_chars} 字符"
        title += " ====="
        # 一次请求含发送和返回。返回写完后空 10 行，下一次请求隔开。
        gap = "\n" * 10 if kind == "返回" else ""
        block = f"\n{title}\n{text.rstrip()}\n{gap}"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(block)

    def _read_stream(self, response) -> ModelTurn:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                content_parts.append(piece)
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if reasoning:
                reasoning_parts.append(reasoning)
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index") or 0)
                slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    slot["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    slot["name"] = function["name"]
                if function.get("arguments"):
                    slot["arguments"] += function["arguments"]
        parsed = [
            {
                "id": slot["id"] or f"call_{uuid.uuid4().hex[:8]}",
                "name": slot["name"],
                "arguments": parse_arguments(slot["arguments"]),
            }
            for index, slot in sorted(calls.items())
        ]
        content = "".join(content_parts)
        if not content.strip() and not parsed:
            content = "".join(reasoning_parts)
        return ModelTurn(content, parsed)

    def _ollama_endpoint(self) -> bool:
        base = self.base_url.lower()
        return ":11434" in base or "ollama" in base
