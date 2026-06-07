"""LLM 适配层 —— LiteLLM 统一对接,运行期可切换模型 (§7)。

设计:
  * 单一入口 complete(messages, tools),使用 OpenAI 兼容格式
  * 不同 provider 的差异由 LiteLLM 抹平 (drop_params=True 自动剥离不支持的参数)
  * 模型可运行期切换 (switch_model),供 UI 模型选择器调用
  * KNOWN_MODELS 注册表 —— UI 模型下拉框的数据源
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import litellm

from app.config import LLMSettings

# LiteLLM 全局配置
litellm.drop_params = True
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ============================== 模型注册表 ==============================

@dataclass(frozen=True)
class ModelInfo:
    """供 UI 模型选择器使用的模型元信息。"""
    id: str                       # litellm model id, 如 deepseek/deepseek-chat
    display: str                  # 中文显示名
    provider: str                 # deepseek/dashscope/anthropic/openai/ollama
    location: str                 # 国内直连 / 公网 / 本地
    cost: str                     # 输入 / 输出 每 M token (¥)
    notes: str
    api_key_env: str              # 检测 key 是否设置用的 env 变量名


# 按推荐顺序排列（默认首选放最前）
KNOWN_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="deepseek/deepseek-chat",
        display="DeepSeek V3 (国内首选)",
        provider="deepseek",
        location="国内直连",
        cost="¥1 / ¥2",
        notes="最便宜、function calling 稳定,推荐起步",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    ModelInfo(
        id="deepseek/deepseek-reasoner",
        display="DeepSeek R1 (推理模型)",
        provider="deepseek",
        location="国内直连",
        cost="¥4 / ¥16",
        notes="深度推理,适合复杂多步任务",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    ModelInfo(
        id="dashscope/qwen-max",
        display="通义千问 Max",
        provider="dashscope",
        location="国内直连 (阿里云)",
        cost="¥20 / ¥60",
        notes="中文最强,企业合规友好",
        api_key_env="DASHSCOPE_API_KEY",
    ),
    ModelInfo(
        id="dashscope/qwen-plus",
        display="通义千问 Plus",
        provider="dashscope",
        location="国内直连 (阿里云)",
        cost="¥4 / ¥12",
        notes="性价比版本",
        api_key_env="DASHSCOPE_API_KEY",
    ),
    ModelInfo(
        id="dashscope/qwen-turbo",
        display="通义千问 Turbo",
        provider="dashscope",
        location="国内直连 (阿里云)",
        cost="¥0.3 / ¥0.6",
        notes="极轻量,适合参数抽取等简单任务",
        api_key_env="DASHSCOPE_API_KEY",
    ),
    ModelInfo(
        id="anthropic/claude-opus-4-7",
        display="Claude Opus 4.7",
        provider="anthropic",
        location="公网 (或 Bedrock)",
        cost="¥36 / ¥180",
        notes="工具规划最强,复杂任务首选",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ModelInfo(
        id="anthropic/claude-sonnet-4-6",
        display="Claude Sonnet 4.6",
        provider="anthropic",
        location="公网 (或 Bedrock)",
        cost="¥22 / ¥108",
        notes="速度/能力平衡",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ModelInfo(
        id="openai/gpt-4o",
        display="OpenAI GPT-4o",
        provider="openai",
        location="公网",
        cost="¥18 / ¥72",
        notes="备选",
        api_key_env="OPENAI_API_KEY",
    ),
    ModelInfo(
        id="ollama/qwen2.5:72b",
        display="Qwen2.5-72B (本地)",
        provider="ollama",
        location="本地部署",
        cost="自托管",
        notes="涉密场景,数据不出企业",
        api_key_env="",
    ),
]


def find_model(model_id: str) -> ModelInfo | None:
    for m in KNOWN_MODELS:
        if m.id == model_id:
            return m
    return None


def model_ready(info: ModelInfo) -> bool:
    """是否已配置该模型所需的 API key。"""
    if not info.api_key_env:
        return True                              # 本地模型不需要 key
    return bool(os.environ.get(info.api_key_env, "").strip())


# ============================== 响应数据结构 ==============================

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None
    model: str = ""                              # 实际调用的模型

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_calls)


# ============================== LLMClient ==============================

class LLMClient:
    """运行期可切换模型的 LLM 客户端。"""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.model = settings.model
        self.api_base = settings.api_base
        self.api_key = settings.api_key
        self.timeout = settings.timeout

    def describe(self) -> str:
        info = find_model(self.model)
        if info:
            return f"LLMClient(model={self.model} [{info.display}], timeout={self.timeout}s)"
        return f"LLMClient(model={self.model}, timeout={self.timeout}s)"

    def switch_model(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """运行期切换模型 —— 供 UI 模型下拉框调用。

        api_key/api_base 不传则保留旧值 (LiteLLM 会自动按 provider 读对应 env)。
        """
        self.model = model.strip()
        if api_key is not None:
            self.api_key = api_key.strip() or None
        if api_base is not None:
            self.api_base = api_base.strip() or None

    def current_status(
        self,
        is_ready: Callable[[ModelInfo], bool] | None = None,
    ) -> dict[str, Any]:
        """UI 调用:当前模型 + 是否有 key + 列出可选模型。

        is_ready: 判定某个模型当前是否可用的回调,默认按 .env 中的 *_API_KEY
                 判断(即进程环境视角)。需要按登录用户判断时,服务端应传入
                 解析私有 key 的回调,这样 current_ready / models[].ready
                 才会反映该用户的真实可用性。
        """
        info = find_model(self.model)
        ready_fn = is_ready or model_ready
        return {
            "current": self.model,
            "current_display": info.display if info else self.model,
            "current_ready": (ready_fn(info) if info else bool(self.api_key)),
            "models": [
                {
                    "id": m.id,
                    "display": m.display,
                    "provider": m.provider,
                    "location": m.location,
                    "cost": m.cost,
                    "notes": m.notes,
                    "ready": ready_fn(m),
                }
                for m in KNOWN_MODELS
            ],
        }

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kw: Any,
    ) -> LLMResponse:
        """调用当前 model；LiteLLM 抹平 provider 差异。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "timeout": self.timeout,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = kw.pop("tool_choice", "auto")
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        kwargs.update(kw)

        raw = litellm.completion(**kwargs)
        resp = _parse_response(raw)
        resp.model = self.model
        return resp


# ============================== Helpers ==============================

def _parse_response(raw: Any) -> LLMResponse:
    choice = raw.choices[0]
    msg = choice.message
    text = msg.content or ""
    finish = (choice.finish_reason or "stop").lower()

    tool_calls: list[ToolCall] = []
    raw_tcs = getattr(msg, "tool_calls", None) or []
    for tc in raw_tcs:
        try:
            args_raw = tc.function.arguments
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            args = {"__raw__": str(args_raw)}
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

    usage = getattr(raw, "usage", None)
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        finish_reason=finish,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        raw=raw,
    )


def assistant_message_from(resp: LLMResponse) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": resp.text or ""}
    if resp.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in resp.tool_calls
        ]
    return msg


def tool_message(tool_call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
