"""pytest 共享 fixtures。"""
from __future__ import annotations

import sys
from pathlib import Path

# 让测试能 import app.*  /  cli.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from app.bw.mock import MockBWClient


@pytest.fixture(scope="session")
def mock_data_dir() -> Path:
    p = ROOT / "mock_data"
    assert p.exists(), f"mock_data 不存在: {p}（请先跑 python mock_data/seed.py）"
    return p


@pytest.fixture
def mock_bw(mock_data_dir: Path) -> MockBWClient:
    return MockBWClient(mock_data_dir, latency_ms=0)
