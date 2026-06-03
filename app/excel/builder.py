"""Excel 生成器 (§10)。

设计:
  * 每个产出至少 2 个 sheet: 数据 / 查询信息（审计冗余）
  * 默认风格: 浅色简洁,粗体表头,数字千分位,冻结首行
  * 一次性 build_workbook 接口,接收行 + 元信息

不在本期范围: Skill 模板填充、图表嵌入（M3 再做）
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


@dataclass
class ExcelResult:
    path: Path
    rows: int
    columns: list[str]
    size_bytes: int


# ============================== 样式常量 ==============================

_HEADER_FILL = PatternFill("solid", fgColor="EEEEEE")
_HEADER_FONT = Font(name="微软雅黑", size=11, bold=True, color="333333")
_BODY_FONT = Font(name="微软雅黑", size=10, color="333333")
_THIN_BORDER = Border(
    left=Side(style="thin", color="DDDDDD"),
    right=Side(style="thin", color="DDDDDD"),
    top=Side(style="thin", color="DDDDDD"),
    bottom=Side(style="thin", color="DDDDDD"),
)
_CENTER = Alignment(horizontal="center", vertical="center")
_RIGHT = Alignment(horizontal="right", vertical="center")
_LEFT = Alignment(horizontal="left", vertical="center")


class ExcelBuilder:
    """openpyxl 简易封装。无外部状态,可重复使用。"""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        *,
        filename: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        labels: dict[str, str] | None = None,
        info: dict[str, Any] | None = None,
        sheet_name: str = "数据",
    ) -> ExcelResult:
        """生成 .xlsx 文件。

        Args:
            filename: 输出文件名（不含路径）
            columns: 列顺序
            rows: 行数据（dict 列表）
            labels: 列名 → 友好标签（用作表头显示）
            info: 「查询信息」sheet 的键值对（提问、OData URL、用时、token 等）
            sheet_name: 主数据 sheet 名

        Returns:
            ExcelResult，含写入文件路径与统计。
        """
        labels = labels or {}
        info = info or {}

        wb = Workbook()
        # 1. 数据 sheet
        ws = wb.active
        ws.title = sheet_name
        _write_data_sheet(ws, columns, rows, labels)

        # 2. 查询信息 sheet（审计冗余）
        meta_ws = wb.create_sheet("查询信息")
        _write_info_sheet(meta_ws, info)

        # 3. 保存
        out_path = self.output_dir / filename
        wb.save(out_path)
        size = out_path.stat().st_size
        return ExcelResult(path=out_path, rows=len(rows), columns=list(columns), size_bytes=size)


# ============================== sheet writer ==============================


def _write_data_sheet(
    ws: Any,
    columns: list[str],
    rows: list[dict[str, Any]],
    labels: dict[str, str],
) -> None:
    if not columns:
        ws.cell(row=1, column=1, value="(无数据)")
        return

    # 表头
    for j, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=j, value=labels.get(col, col))
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER
        cell.border = _THIN_BORDER

    # 数据
    for i, r in enumerate(rows, start=2):
        for j, col in enumerate(columns, start=1):
            val = r.get(col)
            cell = ws.cell(row=i, column=j, value=val)
            cell.font = _BODY_FONT
            cell.border = _THIN_BORDER
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cell.number_format = "#,##0.##" if isinstance(val, float) else "#,##0"
                cell.alignment = _RIGHT
            else:
                cell.alignment = _LEFT

    # 列宽自适应
    for j, col in enumerate(columns, start=1):
        max_len = len(str(labels.get(col, col)))
        for r in rows[:200]:           # 看前 200 行估算够了
            v = r.get(col)
            if v is None:
                continue
            max_len = max(max_len, len(str(v)))
        ws.column_dimensions[get_column_letter(j)].width = min(40, max(8, max_len + 2))

    ws.freeze_panes = "A2"
    ws.sheet_view.zoomScale = 100


def _write_info_sheet(ws: Any, info: dict[str, Any]) -> None:
    rows = [
        ("生成时间", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    # 用户传入的所有键值，按字典顺序但优先排已知字段
    known_order = [
        "username", "question", "skill_id", "skill_version",
        "service", "entity_set", "odata_url",
        "row_count", "latency_ms",
        "llm_model", "llm_input_tokens", "llm_output_tokens",
        "bw_mode",
    ]
    seen: set[str] = set()
    for k in known_order:
        if k in info:
            rows.append((k, info[k]))
            seen.add(k)
    for k, v in info.items():
        if k not in seen:
            rows.append((k, v))

    ws.cell(row=1, column=1, value="字段").font = _HEADER_FONT
    ws.cell(row=1, column=2, value="值").font = _HEADER_FONT
    ws.cell(row=1, column=1).fill = _HEADER_FILL
    ws.cell(row=1, column=2).fill = _HEADER_FILL

    for i, (k, v) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=str(k)).font = _BODY_FONT
        cell = ws.cell(row=i, column=2, value=_stringify(v))
        cell.font = _BODY_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 80


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        import json
        return json.dumps(v, ensure_ascii=False)
    return str(v)
