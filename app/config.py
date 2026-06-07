"""应用配置 —— 集中加载并校验环境变量。

设计原则:
  * 不可变 (frozen dataclass)，启动期一次性加载
  * 严格区分必填 vs 可选
  * BW_MODE=mock 时不强制 BW 连接信息
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _get_int(key: str, default: int) -> int:
    raw = _get(key)
    return int(raw) if raw else default


def _get_bool(key: str, default: bool) -> bool:
    raw = _get(key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class LLMSettings:
    model: str
    api_base: str | None
    api_key: str | None
    timeout: int
    max_iters: int


@dataclass(frozen=True)
class BWSettings:
    mode: str                   # "mock" | "live"
    mock_data_dir: Path
    mock_latency_ms: int
    # live-only
    base_url: str
    username: str
    password: str
    client: str
    language: str
    verify_ssl: bool
    timeout: int


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings
    bw: BWSettings
    output_dir: Path
    skills_dir: Path

    def validate(self) -> list[str]:
        """启动期自检；返回错误列表（空 = OK）。"""
        errors: list[str] = []
        if not self.llm.model:
            errors.append("LLM_MODEL 未设置 (例如 deepseek/deepseek-chat)")
        if self.bw.mode not in ("mock", "live"):
            errors.append(f"BW_MODE 必须是 mock 或 live，当前为 {self.bw.mode!r}")
        if self.bw.mode == "live":
            if not self.bw.base_url:
                errors.append("BW_MODE=live 但 BW_BASE_URL 未设置")
            if not self.bw.username:
                errors.append("BW_MODE=live 但 BW_USERNAME 未设置")
            if not self.bw.password:
                errors.append("BW_MODE=live 但 BW_PASSWORD 未设置")
        if self.bw.mode == "mock" and not self.bw.mock_data_dir.exists():
            errors.append(f"MOCK_DATA_DIR 不存在: {self.bw.mock_data_dir}")
        return errors


def load_settings() -> Settings:
    """从环境变量构造 Settings。"""
    llm = LLMSettings(
        model=_get("LLM_MODEL", "deepseek/deepseek-chat"),
        api_base=_get("LLM_API_BASE") or None,
        api_key=_get("LLM_API_KEY") or None,
        timeout=_get_int("LLM_TIMEOUT", 120),
        max_iters=_get_int("LLM_MAX_ITERS", 10),
    )

    bw = BWSettings(
        mode=_get("BW_MODE", "mock"),
        mock_data_dir=Path(_get("MOCK_DATA_DIR", "./mock_data")).resolve(),
        mock_latency_ms=_get_int("MOCK_LATENCY_MS", 200),
        base_url=_get("BW_BASE_URL").rstrip("/"),
        username=_get("BW_USERNAME"),
        password=_get("BW_PASSWORD"),
        client=_get("BW_CLIENT"),
        language=_get("BW_LANGUAGE", "EN") or "EN",
        verify_ssl=_get_bool("BW_VERIFY_SSL", True),
        timeout=_get_int("BW_TIMEOUT", 60),
    )

    return Settings(
        llm=llm,
        bw=bw,
        output_dir=Path(_get("OUTPUT_DIR", "./data/outputs")).resolve(),
        skills_dir=Path(_get("SKILLS_DIR", "./data/skills")).resolve(),
    )
