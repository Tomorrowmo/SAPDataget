"""Skill 数据结构定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SkillNotFound(KeyError):
    """请求了不存在的 Skill。"""


@dataclass
class SkillParam:
    """Skill 接受的一个参数（从 SKILL.md frontmatter / service.yaml params 抽取）。"""
    name: str
    required: bool = False
    description: str = ""
    default: Any = None
    enum: list[str] | None = None


@dataclass
class Skill:
    """单个 Skill 模板。

    布局参见 §9.2:
        data/skills/<skill_id>/
          SKILL.md         必填,frontmatter + 描述 + 给 LLM 的指引
          service.yaml     必填,数据源 + filter 模板
          template.xlsx    选填,Excel 模板（v0.2 暂不支持模板填充,M3 再做）
    """
    id: str
    version: int
    title: str
    description: str
    owner: str = ""
    keywords: list[str] = field(default_factory=list)
    visible_to: list[str] = field(default_factory=list)         # 角色组白名单
    params: list[SkillParam] = field(default_factory=list)
    instructions: str = ""                                       # 给 LLM 的额外指引(SKILL.md 的正文)

    # 数据源 (service.yaml)
    service: str = ""
    entity_set: str = ""
    filter_template: str = ""                                   # jinja2 占位符
    select: list[str] = field(default_factory=list)
    orderby: str = ""
    top: str | int | None = 100                                 # 可以是字符串模板
    apply: str = ""

    # 输出
    sheet_title: str = "数据"
    folder_path: Path | None = None

    def to_summary(self) -> dict[str, Any]:
        """LLM list_skills 时返回的简要描述。"""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "keywords": self.keywords,
            "params": [
                {
                    "name": p.name,
                    "required": p.required,
                    "description": p.description,
                    "default": p.default,
                    "enum": p.enum,
                }
                for p in self.params
            ],
        }

    def to_detail(self) -> dict[str, Any]:
        """LLM load_skill 时返回的完整定义（除了 Excel 模板二进制）。"""
        return {
            **self.to_summary(),
            "owner": self.owner,
            "instructions": self.instructions,
            "service": self.service,
            "entity_set": self.entity_set,
            "filter_template": self.filter_template,
            "select": self.select,
            "orderby": self.orderby,
            "top": self.top,
            "apply": self.apply,
            "sheet_title": self.sheet_title,
        }
