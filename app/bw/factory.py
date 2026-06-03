"""BWClient 工厂 —— 根据 settings 选择 Live 或 Mock 实现。"""
from __future__ import annotations

from app.bw.interface import BWClient
from app.config import Settings


def make_bw_client(settings: Settings) -> BWClient:
    """按 BW_MODE 实例化对应的 BWClient。"""
    mode = settings.bw.mode
    if mode == "mock":
        # 延迟导入,避免不需要 pandas 的场景被强加依赖
        from app.bw.mock import MockBWClient
        return MockBWClient(
            data_dir=settings.bw.mock_data_dir,
            latency_ms=settings.bw.mock_latency_ms,
        )
    if mode == "live":
        from app.bw.live import LiveBWClient
        return LiveBWClient(settings.bw)
    raise ValueError(f"未知 BW_MODE: {mode!r}（应为 mock 或 live）")
