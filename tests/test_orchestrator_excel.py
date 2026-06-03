"""Orchestrator + ExcelBuilder 端到端测试 (不接 LLM)。"""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.bw.mock import MockBWClient
from app.config import BWSettings, LLMSettings, Settings
from app.orchestrator import TaskOrchestrator
from app.skills.registry import SkillRegistry


@pytest.fixture
def settings(tmp_path: Path, mock_data_dir: Path) -> Settings:
    return Settings(
        llm=LLMSettings(model="dummy/none", api_base=None, api_key=None, timeout=10, max_iters=3),
        bw=BWSettings(
            mode="mock",
            mock_data_dir=mock_data_dir,
            mock_latency_ms=0,
            base_url="", username="", password="", client="",
            language="EN", verify_ssl=True, timeout=60,
        ),
        output_dir=tmp_path / "outputs",
        skills_dir=Path(__file__).resolve().parent.parent / "data" / "skills",
    )


@pytest.fixture
def orch(settings: Settings, mock_bw: MockBWClient) -> TaskOrchestrator:
    skills = SkillRegistry(settings.skills_dir)
    skills.reload()
    return TaskOrchestrator(settings, mock_bw, skills)


def test_run_skill_produces_excel(orch: TaskOrchestrator, tmp_path: Path):
    result = orch.run_skill(
        "monthly_sales_region",
        {"month": "202605", "region": "HD", "top_n": 5},
        username="alice",
    )
    assert result.status == "done", result.error
    assert result.excel is not None
    assert result.excel.path.exists()
    assert result.excel.size_bytes > 1000          # 真的写了内容
    assert result.row_count >= 1

    # 验证 xlsx 内容
    wb = load_workbook(result.excel.path)
    assert "查询信息" in wb.sheetnames
    data_ws = wb[wb.sheetnames[0]]
    # 表头
    headers = [c.value for c in data_ws[1]]
    assert any(h and "销售" in str(h) for h in headers), f"应有中文 label: {headers}"
    # 行数
    data_rows = list(data_ws.iter_rows(min_row=2, values_only=True))
    assert len(data_rows) == result.row_count

    # 查询信息 sheet
    info_ws = wb["查询信息"]
    info_dict = {}
    for r in info_ws.iter_rows(min_row=2, values_only=True):
        if r[0]:
            info_dict[r[0]] = r[1]
    assert info_dict.get("username") == "alice"
    assert info_dict.get("skill_id") == "monthly_sales_region"
    assert info_dict.get("bw_mode") == "mock"


def test_run_skill_unknown_id(orch: TaskOrchestrator):
    result = orch.run_skill("nosuch", {}, username="bob")
    assert result.status == "failed"
    assert "Skill" in (result.error or "")


def test_run_skill_empty_result(orch: TaskOrchestrator):
    """查询条件不匹配任何数据 → 友好失败。"""
    result = orch.run_skill(
        "monthly_sales_region",
        {"month": "190001", "region": "HD"},     # 1900-01 mock 里没有
        username="bob",
    )
    assert result.status == "failed"
    assert result.error is not None
