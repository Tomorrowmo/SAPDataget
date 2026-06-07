"""SkillRegistry —— 扫描 data/skills/ 目录加载所有 Skill。

文件格式:
  <id>/SKILL.md      YAML frontmatter + 中文正文
  <id>/service.yaml  数据源定义
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.skills.schema import Skill, SkillNotFound, SkillParam

log = logging.getLogger(__name__)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class SkillRegistry:
    """内存中维护所有 active Skill。"""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir).resolve()
        self._skills: dict[str, Skill] = {}

    def reload(self) -> int:
        """重新扫描目录，返回加载到的 Skill 数。"""
        self._skills.clear()
        if not self.skills_dir.exists():
            log.warning("skills_dir 不存在: %s", self.skills_dir)
            return 0

        for sub in sorted(self.skills_dir.iterdir()):
            if not sub.is_dir():
                continue
            try:
                skill = _load_skill(sub)
                self._skills[skill.id] = skill
            except Exception as e:                                # noqa: BLE001
                log.warning("加载 Skill 失败 %s: %s", sub.name, e)
        return len(self._skills)

    def list(self, keywords: list[str] | None = None, role: str | None = None) -> list[Skill]:
        out = list(self._skills.values())
        if role:
            out = [s for s in out if not s.visible_to or role in s.visible_to]
        if keywords:
            kw = [k.lower() for k in keywords]
            def _match(s: Skill) -> bool:
                hay = " ".join([s.id, s.title, s.description, *s.keywords]).lower()
                return any(k in hay for k in kw)
            out = [s for s in out if _match(s)]
        return out

    def get(self, skill_id: str) -> Skill:
        if skill_id not in self._skills:
            raise SkillNotFound(skill_id)
        return self._skills[skill_id]

    def __len__(self) -> int:
        return len(self._skills)


# ============================== 加载单个 Skill ==============================


def _load_skill(folder: Path) -> Skill:
    skill_md = folder / "SKILL.md"
    service_yaml = folder / "service.yaml"
    if not skill_md.exists():
        raise FileNotFoundError(f"{folder.name} 缺 SKILL.md")
    if not service_yaml.exists():
        raise FileNotFoundError(f"{folder.name} 缺 service.yaml")

    fm, instructions = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    svc = yaml.safe_load(service_yaml.read_text(encoding="utf-8")) or {}

    params: list[SkillParam] = []
    for p in fm.get("params", []) or []:
        params.append(SkillParam(
            name=p["name"],
            required=bool(p.get("required", False)),
            description=p.get("description", ""),
            default=p.get("default"),
            enum=p.get("enum"),
        ))

    skill = Skill(
        id=fm.get("id") or folder.name,
        version=int(fm.get("version", 1)),
        title=fm.get("title", folder.name),
        description=fm.get("description", ""),
        owner=fm.get("owner", ""),
        keywords=list(fm.get("keywords", []) or []),
        visible_to=list(fm.get("visible_to", []) or []),
        params=params,
        instructions=instructions.strip(),
        service=svc.get("service", ""),
        entity_set=svc.get("entity_set", ""),
        filter_template=svc.get("filter_template", ""),
        select=list(svc.get("select", []) or []),
        orderby=svc.get("orderby", "") or "",
        top=svc.get("top", 100),
        apply=svc.get("apply", "") or "",
        sheet_title=svc.get("sheet_title", "数据"),
        folder_path=folder,
    )
    if not skill.service or not skill.entity_set:
        raise ValueError(f"{folder.name} service.yaml 缺 service/entity_set")
    return skill


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """提取 SKILL.md 的 YAML frontmatter + 正文。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        return {}, text
    return fm, m.group(2)
