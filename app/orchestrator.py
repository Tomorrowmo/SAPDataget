"""TaskOrchestrator —— 把 BW / Skills / Excel / LLM 串成一个 Task。

一次 Task 状态机 (§6.2):

  created → running → done / failed
  created → running → awaiting_user (LLM 追问参数) → running → ...

本期实现 CLI/单机版,后续 FastAPI 直接复用同一编排器。
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.bw.interface import BWClient
from app.config import Settings
from app.excel.builder import ExcelBuilder, ExcelResult, load_chart_config
from app.rowsec import scope_rows_to_user
from app.skills.registry import SkillRegistry
from app.skills.runner import SkillRunner
from app.skills.schema import Skill

log = logging.getLogger(__name__)


@dataclass
class TaskResult:
    task_id: str
    status: str                                # 'done' | 'failed'
    excel: ExcelResult | None = None
    rows_preview: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class TaskOrchestrator:
    """协调一次「取数任务」的全过程。"""

    def __init__(
        self,
        settings: Settings,
        bw: BWClient,
        skills: SkillRegistry,
        sensitive_fields_resolver: Any | None = None,
    ) -> None:
        """sensitive_fields_resolver: 可选回调 (service: str) -> dict[field, mask_mode]。
        允许 server 注入 DB-backed 的脱敏配置;CLI 不传则不脱敏。
        """
        self.settings = settings
        self.bw = bw
        self.skills = skills
        self.skill_runner = SkillRunner(bw)
        self.excel = ExcelBuilder(settings.output_dir)
        self._sensitive_resolver = sensitive_fields_resolver

    def _resolve_sensitive(self, service: str) -> dict[str, str]:
        if self._sensitive_resolver is None or not service:
            return {}
        try:
            return self._sensitive_resolver(service) or {}
        except Exception as e:                                # noqa: BLE001
            log.warning("敏感字段解析失败 service=%s: %s", service, e)
            return {}

    # ---------- Skill 驱动路径（高频） ----------
    def run_skill(
        self,
        skill_id: str,
        params: dict[str, Any],
        *,
        username: str = "cli_user",
        question: str = "",
    ) -> TaskResult:
        task_id = _task_id(skill_id)
        t0 = time.monotonic()
        try:
            skill = self.skills.get(skill_id)
        except KeyError:
            return TaskResult(task_id=task_id, status="failed", error=f"未找到 Skill: {skill_id}")

        try:
            result = self.skill_runner.run(skill, params)
        except ValueError as e:
            return TaskResult(task_id=task_id, status="failed", error=str(e))

        if result.response.error:
            return TaskResult(
                task_id=task_id, status="failed",
                error=result.response.error,
                meta={"odata_url": result.response.url},
            )

        data = result.response.json or {}
        rows: list[dict[str, Any]] = data.get("rows", []) or []
        if not rows:
            return TaskResult(
                task_id=task_id, status="failed",
                error="查询无数据 —— 请检查参数（如月份、大区是否存在）",
                meta={"odata_url": result.response.url, "params": result.params},
            )

        # 行级归属过滤:结果含归属字段(UName)则只保留登录用户名下的行(Excel + 预览都裁)。
        rows, scoped = scope_rows_to_user(rows, self.settings.owner_field, username)
        if scoped and not rows:
            return TaskResult(
                task_id=task_id, status="failed",
                error=f"查询结果中没有属于你（{username}）的数据。",
                meta={"odata_url": result.response.url, "params": result.params},
            )

        # 构造 Excel
        columns = skill.select if skill.select else list(rows[0].keys())
        labels = _column_labels(skill, self.bw)
        latency_ms = int((time.monotonic() - t0) * 1000)
        # 归属过滤后,行数用裁剪后的实数(总数=全用户总数会误导)。
        row_count = len(rows) if scoped else (data.get("row_count_total") or len(rows))
        info = {
            "username": username,
            "question": question or f"[skill] {skill.title}",
            "skill_id": skill.id,
            "skill_version": skill.version,
            "service": skill.service,
            "entity_set": skill.entity_set,
            "odata_url": result.response.url,
            "row_count": row_count,
            "latency_ms": latency_ms,
            "bw_mode": self.settings.bw.mode,
            "rendered_filter": result.rendered_filter,
            "owner_scoped": scoped,
        }
        filename = _filename(skill.id, username)
        template_path = None
        if skill.folder_path:
            t = Path(skill.folder_path) / "template.xlsx"
            if t.exists():
                template_path = t
        chart_cfg = load_chart_config(skill.folder_path)
        excel_result = self.excel.build(
            filename=filename,
            columns=columns,
            rows=rows,
            labels=labels,
            info=info,
            sheet_name=skill.sheet_title,
            sensitive_fields=self._resolve_sensitive(skill.service),
            template_path=template_path,
            chart=chart_cfg,
        )

        return TaskResult(
            task_id=task_id,
            status="done",
            excel=excel_result,
            rows_preview=rows[:20],
            row_count=info["row_count"],
            meta=info,
        )

    # ---------- 自由模式（无 Skill,LLM 直接拼） ----------
    def run_free_query(
        self,
        *,
        service: str,
        entity_set: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        info: dict[str, Any],
        sheet_title: str = "数据",
        username: str = "cli_user",
    ) -> TaskResult:
        """LLM 已经在自由模式下拿到结果,这里只负责打包成 Excel。"""
        task_id = _task_id("free")
        filename = _filename(f"free_{service}_{entity_set}", username)
        # 行级归属过滤:结果含 UName 则只保留登录用户名下的行(Excel + 预览)。
        rows, scoped = scope_rows_to_user(rows, self.settings.owner_field, username)
        merged_info = {**info, "bw_mode": self.settings.bw.mode, "username": username}
        if scoped:
            merged_info["row_count"] = len(rows)
            merged_info["owner_scoped"] = True
        excel_result = self.excel.build(
            filename=filename,
            columns=columns,
            rows=rows,
            labels=None,
            info=merged_info,
            sheet_name=sheet_title,
            sensitive_fields=self._resolve_sensitive(service),
        )
        return TaskResult(
            task_id=task_id,
            status="done",
            excel=excel_result,
            rows_preview=rows[:20],
            row_count=merged_info.get("row_count", len(rows)),
            meta=merged_info,
        )


# ============================== Helpers ==============================


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_\-]")


def _safe(s: str) -> str:
    return _SAFE_NAME.sub("_", s)


def _task_id(prefix: str) -> str:
    return f"t_{_safe(prefix)}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _filename(skill_id: str, username: str) -> str:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_safe(skill_id)}_{ts}_{_safe(username)}.xlsx"


def _column_labels(skill: Skill, bw: BWClient) -> dict[str, str]:
    """从 BW metadata 取字段中文 label，用于 Excel 表头。"""
    labels: dict[str, str] = {}
    if not skill.service or not skill.entity_set:
        return labels
    meta_resp = bw.get_metadata(skill.service)
    if meta_resp.error or not meta_resp.json:
        return labels
    for es in meta_resp.json.get("entity_sets", []) or []:
        if es.get("name") == skill.entity_set:
            for p in es.get("properties", []) or []:
                if p.get("label"):
                    labels[p["name"]] = p["label"]
    return labels
