"""行级数据归属过滤 —— 只把"属于登录用户"的行返回给用户。

与数据源(mock/live)无关、与具体字段值无关:**按"结果里是否含归属字段"自适应**。
结果行含 owner_field(默认 UName)→ 只保留归属=登录用户的行;不含 → 原样(交给 BW 授权)。
归一化口径与前端 filterRowsByLoginUser 一致(大写 + 去空格),容忍大小写/空格差异。
"""
from __future__ import annotations

from typing import Any


def _norm(v: Any) -> str:
    return "".join(str(v if v is not None else "").upper().split())


def _owner_key(row: dict[str, Any], owner_field: str) -> str | None:
    """在行里找归属列(不区分大小写),返回真实键名;找不到返回 None。"""
    target = owner_field.lower()
    for k in row.keys():
        if k.lower() == target:
            return k
    return None


def scope_rows_to_user(
    rows: list[dict[str, Any]],
    owner_field: str,
    username: str,
) -> tuple[list[dict[str, Any]], bool]:
    """把 rows 裁剪为只属于 username 的行。

    Returns (scoped_rows, applied):
      applied=True  → 结果含归属字段,已按用户裁剪;
      applied=False → 不含归属字段,原样返回(由 BW Analysis Authorization 兜底)。
    """
    if not rows or not owner_field or not username:
        return rows, False
    key = _owner_key(rows[0], owner_field)
    if key is None:
        return rows, False
    me = _norm(username)
    scoped = [r for r in rows if _norm(r.get(key)) == me]
    return scoped, True
