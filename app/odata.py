"""OData V2 共享工具 —— 与数据源(mock/live)完全无关的纯逻辑。

把"正确性"集中在这里,Live 与 Mock 复用同一套规则,杜绝把"迁就 mock 干净数据"
的假设写进生产代码:

  * odata_quote / edm_literal —— OData 字符串字面量单引号转义('→''),按 Edm 类型生成字面量
  * build_filter            —— 结构化条件 [{field,op,value}] → 安全 $filter(防注入)
  * coerce_edm_value/rows   —— 把 OData V2 编码(Decimal 字符串、/Date(ms)/、datetime'...')转真实类型
  * parse_odata_error       —— 从 SAP 错误体(JSON error.message.value / XML <error><message>)提取可读信息
  * prop_types              —— 从简化 $metadata 抽 {field: Edm 类型}

接真 SAP 后,本模块对"真实编码形状"的处理须用真实响应体复核(见分析文档 §0/§4)。
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import re as _re
from typing import Any
from xml.etree import ElementTree as _ET

# 二元比较运算符(可直接出现在 $filter)
_CMP_OPS = {"eq", "ne", "gt", "ge", "lt", "le"}
# 函数式运算符(转成 OData 函数调用)
_FUNC_OPS = {"contains", "substringof", "startswith", "endswith"}


# ============================== 字面量与转义 ==============================

def odata_quote(value: Any) -> str:
    """OData 字符串字面量:单引号转义(' → '')并用单引号包裹。

    这是防 OData 注入与"撇号崩溃"的唯一正确做法。
    例: O'Brien → 'O''Brien'  ;  HD' or '1'='1 → 'HD'' or ''1''=''1'
    """
    return "'" + str(value).replace("'", "''") + "'"


def _to_edm_datetime(value: Any) -> str:
    """把日期值规整为 OData V2 datetime 字面量内容(不含 datetime'' 包裹)。

    接受 'YYYY-MM-DD' / 'YYYY-MM-DDTHH:MM:SS' / date / datetime。
    """
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%dT00:00:00")
    s = str(value).strip()
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s + "T00:00:00"
    return s


def edm_literal(value: Any, edm_type: str | None) -> str:
    """按 Edm 类型把一个值渲染成 OData 字面量。

    字符串/GUID/未知 → 转义引号(防注入,默认从严);
    数值/布尔 → 裸值;DateTime → datetime'...'。
    """
    if value is None:
        return "null"
    t = (edm_type or "").lower()
    if "datetime" in t:                       # Edm.DateTime / DateTimeOffset
        return "datetime'" + _to_edm_datetime(value) + "'"
    if any(k in t for k in ("decimal", "double", "single", "float", "int", "byte")):
        # 数值:裸值;非法数值降级为转义字符串,避免拼出非法 $filter
        s = str(value).strip()
        if _re.fullmatch(r"-?\d+(\.\d+)?", s):
            return s
        return odata_quote(value)
    if "boolean" in t:
        if isinstance(value, bool):
            return "true" if value else "false"
        return "true" if str(value).strip().lower() in ("true", "1", "x", "yes") else "false"
    # Edm.String / Edm.Guid / 未知 → 一律转义引号(从严防注入)
    return odata_quote(value)


def build_filter(conditions: list[dict[str, Any]], types: dict[str, str] | None = None) -> str:
    """把结构化条件安全拼成 OData V2 $filter(以 and 连接)。

    conditions: [{"field": str, "op": str, "value": Any}], op ∈
        eq|ne|gt|ge|lt|le|contains|substringof|startswith|endswith
    types: {field: Edm 类型}(来自 $metadata),决定字面量格式;缺省按 Edm.String 从严转义。

    字段名只允许 [A-Za-z0-9_/](OData 标识符,/ 用于 BW 的 /BIC/ 命名),否则拒绝 —— 防止
    把注入塞进字段位。
    """
    types = types or {}
    parts: list[str] = []
    for c in conditions or []:
        field = str(c.get("field", "")).strip()
        op = str(c.get("op", "eq")).strip().lower()
        value = c.get("value")
        if not _re.fullmatch(r"[A-Za-z0-9_/]+", field):
            raise ValueError(f"非法字段名: {field!r}")
        t = types.get(field, "Edm.String")
        if op in _CMP_OPS:
            parts.append(f"{field} {op} {edm_literal(value, t)}")
        elif op in ("contains", "substringof"):
            parts.append(f"substringof({odata_quote(value)},{field})")
        elif op == "startswith":
            parts.append(f"startswith({field},{odata_quote(value)})")
        elif op == "endswith":
            parts.append(f"endswith({field},{odata_quote(value)})")
        else:
            raise ValueError(f"不支持的操作符: {op!r}")
    return " and ".join(parts)


# ============================== Edm 类型转换 ==============================

_DATE_MS_RE = _re.compile(r"/Date\((-?\d+)([+-]\d+)?\)/")


def _parse_edm_datetime(value: Any) -> Any:
    """解析 OData V2 日期编码为 datetime;无法解析则原样返回。

    支持: /Date(1714521600000)/、/Date(1714521600000+0000)/、ISO 字符串、date/datetime。
    """
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value
    s = str(value).strip()
    m = _DATE_MS_RE.fullmatch(s)
    if m:
        ms = int(m.group(1))
        return _dt.datetime.utcfromtimestamp(ms / 1000.0)
    # datetime'...' 字面量
    if s.lower().startswith("datetime'") and s.endswith("'"):
        s = s[len("datetime'"):-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return value


def coerce_edm_value(value: Any, edm_type: str | None) -> Any:
    """把单个 OData 编码值转成真实 Python 类型;失败则原样返回(不抛)。"""
    if value is None:
        return None
    t = (edm_type or "").lower()
    try:
        if any(k in t for k in ("decimal", "double", "single", "float")):
            s = str(value).strip()
            return float(s) if s != "" else None
        if "int" in t or "byte" in t:
            s = str(value).strip()
            return int(float(s)) if s != "" else None
        if "boolean" in t:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("true", "1", "x", "yes")
        if "datetime" in t:
            return _parse_edm_datetime(value)
    except (ValueError, TypeError):
        return value
    return value


def coerce_rows(rows: list[dict[str, Any]], prop_types: dict[str, str]) -> list[dict[str, Any]]:
    """对一批行按字段 Edm 类型整体转换。prop_types 为空则原样返回。"""
    if not prop_types or not rows:
        return rows
    out: list[dict[str, Any]] = []
    for r in rows:
        rr = dict(r)
        for k, v in r.items():
            t = prop_types.get(k)
            if t:
                rr[k] = coerce_edm_value(v, t)
        out.append(rr)
    return out


def prop_types(metadata_json: dict[str, Any] | None, entity_set: str) -> dict[str, str]:
    """从简化 $metadata({entity_sets:[{name,properties:[{name,type}]}]})抽 {field: Edm 类型}。"""
    if not isinstance(metadata_json, dict):
        return {}
    for es in metadata_json.get("entity_sets", []) or []:
        if es.get("name") == entity_set:
            return {
                p.get("name"): p.get("type")
                for p in (es.get("properties", []) or [])
                if p.get("name") and p.get("type")
            }
    return {}


# ============================== SAP 错误体解析 ==============================

def parse_odata_error(text: str, content_type: str = "") -> str | None:
    """从 SAP Gateway 错误体提取人类可读的报错。无法解析返回 None。

    JSON: {"error":{"code":"...","message":{"lang":"en","value":"Property 'X' not found"}}}
          或 {"error":{"message":"..."}}
    XML : <error><code>..</code><message xml:lang="en">..</message></error>
    """
    if not text:
        return None
    s = text.strip()
    ct = (content_type or "").lower()
    # 先按内容猜:JSON 以 { 开头,XML 以 < 开头
    if s.startswith("{") or "json" in ct:
        try:
            obj = _json.loads(s)
            err = obj.get("error") if isinstance(obj, dict) else None
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, dict):
                    val = msg.get("value")
                    if val:
                        return str(val).strip()
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
                code = err.get("code")
                if code:
                    return str(code).strip()
        except (ValueError, AttributeError):
            pass
    if s.startswith("<") or "xml" in ct:
        try:
            root = _ET.fromstring(s)
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "message":
                    if node.text and node.text.strip():
                        return node.text.strip()
        except _ET.ParseError:
            pass
    return None
