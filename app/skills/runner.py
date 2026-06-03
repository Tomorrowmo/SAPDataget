"""SkillRunner —— 执行一个 Skill: 拼 OData 参数 → 调 BWClient → 准备给 ExcelBuilder。

接受 params dict（已通过 LLM/用户填好），用 jinja2 sandboxed 渲染模板。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jinja2.sandbox import SandboxedEnvironment

from app.bw.interface import BWClient, ODataResponse
from app.skills.schema import Skill

_jinja_env = SandboxedEnvironment(autoescape=False)


@dataclass
class SkillRunResult:
    skill: Skill
    params: dict[str, Any]
    response: ODataResponse
    rendered_filter: str
    rendered_top: int


class SkillRunner:
    """无状态执行器，可被 Orchestrator 复用。"""

    def __init__(self, bw: BWClient) -> None:
        self.bw = bw

    def run(self, skill: Skill, params: dict[str, Any]) -> SkillRunResult:
        # 1. 参数校验
        normalized = _validate_and_normalize(skill, params)

        # 2. 渲染 filter / top
        try:
            rendered_filter = (
                _jinja_env.from_string(skill.filter_template).render(**normalized)
                if skill.filter_template else ""
            )
            top_value = skill.top
            if isinstance(top_value, str):
                top_value = _jinja_env.from_string(top_value).render(**normalized)
            rendered_top = int(top_value) if top_value else 100
        except Exception as e:
            return SkillRunResult(
                skill=skill,
                params=normalized,
                response=ODataResponse(400, url="", error=f"参数模板渲染失败: {e}"),
                rendered_filter="",
                rendered_top=100,
            )

        # 3. 执行查询
        resp = self.bw.execute_query(
            service=skill.service,
            entity_set=skill.entity_set,
            filter=rendered_filter or None,
            select=",".join(skill.select) if skill.select else None,
            orderby=skill.orderby or None,
            top=rendered_top,
            apply=skill.apply or None,
            count=True,
        )
        return SkillRunResult(
            skill=skill,
            params=normalized,
            response=resp,
            rendered_filter=rendered_filter,
            rendered_top=rendered_top,
        )


def _validate_and_normalize(skill: Skill, params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in skill.params:
        if p.name in params and params[p.name] is not None and params[p.name] != "":
            v = params[p.name]
            if p.enum and str(v) not in [str(e) for e in p.enum]:
                raise ValueError(f"参数 {p.name}={v!r} 不在允许值 {p.enum} 中")
            out[p.name] = v
        elif p.default is not None:
            out[p.name] = p.default
        elif p.required:
            raise ValueError(f"必填参数缺失: {p.name} ({p.description})")
    # 额外参数也透传（让 filter 模板能用）
    for k, v in params.items():
        if k not in out:
            out[k] = v
    return out
