"""Skills 子系统测试 —— Registry + Runner。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bw.mock import MockBWClient
from app.skills.registry import SkillRegistry
from app.skills.runner import SkillRunner
from app.skills.schema import SkillNotFound


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry(Path(__file__).resolve().parent.parent / "data" / "skills")
    r.reload()
    return r


def test_registry_loads_seeds(registry: SkillRegistry):
    skills = registry.list()
    ids = {s.id for s in skills}
    # 至少包含我们种入的 3 个
    assert {"monthly_sales_region", "top_customers", "plant_yield"}.issubset(ids)


def test_registry_search(registry: SkillRegistry):
    matched = registry.list(keywords=["销售"])
    assert any(s.id == "monthly_sales_region" for s in matched)


def test_registry_get_not_found(registry: SkillRegistry):
    with pytest.raises(SkillNotFound):
        registry.get("nosuch_skill_id")


def test_skill_summary_contains_params(registry: SkillRegistry):
    s = registry.get("monthly_sales_region")
    summary = s.to_summary()
    pname = {p["name"] for p in summary["params"]}
    assert {"month", "region"}.issubset(pname)


def test_runner_executes_skill(mock_bw: MockBWClient, registry: SkillRegistry):
    runner = SkillRunner(mock_bw)
    skill = registry.get("monthly_sales_region")
    result = runner.run(skill, {"month": "202605", "region": "HD", "top_n": 5})
    assert result.response.ok, result.response.error
    rows = result.response.json["rows"]
    assert len(rows) <= 5
    for r in rows:
        assert r["Region"] == "HD"
        assert str(r["CALMONTH"]) == "202605"
    # 按 NETWR_F desc
    revs = [r["NETWR_F"] for r in rows]
    assert revs == sorted(revs, reverse=True)


def test_runner_validates_required(mock_bw: MockBWClient, registry: SkillRegistry):
    runner = SkillRunner(mock_bw)
    skill = registry.get("monthly_sales_region")
    with pytest.raises(ValueError):
        runner.run(skill, {"region": "HD"})           # 缺 month


def test_runner_validates_enum(mock_bw: MockBWClient, registry: SkillRegistry):
    runner = SkillRunner(mock_bw)
    skill = registry.get("monthly_sales_region")
    with pytest.raises(ValueError):
        runner.run(skill, {"month": "202605", "region": "BAD"})


def test_runner_optional_param(mock_bw: MockBWClient, registry: SkillRegistry):
    """top_customers 的 region 是可选 —— 不传也能跑。"""
    runner = SkillRunner(mock_bw)
    skill = registry.get("top_customers")
    result = runner.run(skill, {"month": "202605", "top_n": 5})
    assert result.response.ok, result.response.error
    assert len(result.response.json["rows"]) <= 5
