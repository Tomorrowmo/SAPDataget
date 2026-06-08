"""MockBWClient —— 离线 Mock 实现 (§8.4)。

从 mock_data/ 目录读取:
  catalog.json                                  服务清单
  services/<SERVICE>/meta.json                  EntitySet + properties + keys
  services/<SERVICE>/data/<EntitySet>.csv       数据行

对 OData 查询参数在内存中解释执行:
  $select / $top / $skip / $orderby / $filter / $inlinecount

不支持的(明确拒绝并给提示，而非静默):
  $expand
  $apply 复杂聚合 (M3 再做)
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

import pandas as pd

from app.bw.interface import BWClient, ODataResponse
from app.bw.odata_filter import (
    FilterParseError,
    apply_filter,
    apply_orderby,
    apply_select,
)

_FAKE_BASE = "mock://bw.local"


class MockBWClient(BWClient):
    """从本地 mock_data 目录读数据,内存执行 OData 查询。"""

    def __init__(self, data_dir: Path, latency_ms: int = 200) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.latency_ms = max(0, latency_ms)
        self._catalog: dict[str, Any] | None = None
        self._meta_cache: dict[str, dict[str, Any]] = {}
        self._data_cache: dict[tuple[str, str], pd.DataFrame] = {}

    # ---------- 真实感模拟 ----------
    def _sleep(self) -> None:
        if self.latency_ms > 0:
            time.sleep(random.uniform(self.latency_ms / 2000, self.latency_ms / 1000))

    # ---------- 资源加载（懒加载 + 缓存） ----------
    def _load_catalog(self) -> dict[str, Any]:
        if self._catalog is None:
            path = self.data_dir / "catalog.json"
            if not path.exists():
                raise FileNotFoundError(f"找不到 catalog.json: {path}")
            self._catalog = json.loads(path.read_text(encoding="utf-8"))
        return self._catalog

    def _load_meta(self, service: str) -> dict[str, Any]:
        if service not in self._meta_cache:
            path = self.data_dir / "services" / service / "meta.json"
            if not path.exists():
                raise FileNotFoundError(f"找不到 meta.json: {path}")
            self._meta_cache[service] = json.loads(path.read_text(encoding="utf-8"))
        return self._meta_cache[service]

    def _load_data(self, service: str, entity_set: str) -> pd.DataFrame:
        key = (service, entity_set)
        if key not in self._data_cache:
            path = self.data_dir / "services" / service / "data" / f"{entity_set}.csv"
            if not path.exists():
                raise FileNotFoundError(f"找不到数据 CSV: {path}")
            # 全部以 str 读入,再按 meta.json 的 Edm 类型转换 ——
            # 避免 CALMONTH/OfficeCode/KUNNR 这类「ID 类数字串」被自动转 int。
            df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
            numeric_cols, int_cols = self._infer_numeric_columns(service, entity_set)
            for col in df.columns:
                if col in numeric_cols:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                    if col in int_cols:
                        # 允许 NaN 用 Int64 (pandas nullable int)
                        df[col] = df[col].astype("Int64")
            self._data_cache[key] = df
        return self._data_cache[key].copy()

    def _infer_numeric_columns(self, service: str, entity_set: str) -> tuple[set[str], set[str]]:
        """从 meta.json 决定哪些列要转数值。

        Returns:
            (numeric_cols, int_cols) —— 都属于 numeric_cols,int_cols 是其子集
        """
        try:
            meta = self._load_meta(service)
        except FileNotFoundError:
            return set(), set()
        numeric: set[str] = set()
        ints: set[str] = set()
        for es in meta.get("entity_sets", []) or []:
            if es.get("name") != entity_set:
                continue
            for p in es.get("properties", []) or []:
                t = (p.get("type") or "").lower()
                name = p.get("name")
                if not name:
                    continue
                if any(k in t for k in ("decimal", "double", "single", "float")):
                    numeric.add(name)
                elif "int" in t or "byte" in t:
                    numeric.add(name)
                    ints.add(name)
        return numeric, ints

    # ---------- BWClient 接口 ----------
    def describe(self) -> str:
        return f"MockBWClient(data_dir={self.data_dir}, latency={self.latency_ms}ms)"

    def list_services(self, search: str | None = None, top: int = 50) -> ODataResponse:
        self._sleep()
        try:
            catalog = self._load_catalog()
        except FileNotFoundError as e:
            return ODataResponse(500, f"{_FAKE_BASE}/catalog", error=str(e))

        services = catalog.get("services", []) or []
        if search:
            s = search.lower()
            services = [
                x for x in services
                if s in (x.get("TechnicalServiceName") or "").lower()
                or s in (x.get("Title") or "").lower()
                or s in (x.get("Description") or "").lower()
            ]
        services = services[: max(1, top)]

        url = (
            f"{_FAKE_BASE}/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/"
            f"ServiceCollection?$top={top}"
            + (f"&search={search}" if search else "")
        )
        return ODataResponse(
            status_code=200,
            url=url,
            json={"services": services, "count": len(services)},
        )

    def get_metadata(self, service: str) -> ODataResponse:
        self._sleep()
        url = f"{_FAKE_BASE}/sap/opu/odata/sap/{service}/$metadata"
        try:
            meta = self._load_meta(service)
        except FileNotFoundError as e:
            return ODataResponse(404, url, error=f"服务不存在或缺 meta.json: {e}")
        return ODataResponse(status_code=200, url=url, json=meta)

    def execute_query(
        self,
        service: str,
        entity_set: str,
        *,
        filter: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        top: int | None = 100,
        skip: int | None = None,
        expand: str | None = None,
        apply: str | None = None,
        count: bool = False,
    ) -> ODataResponse:
        self._sleep()
        url = _build_mock_url(service, entity_set, filter, select, orderby, top, skip, expand, apply, count)

        if expand:
            return ODataResponse(
                400, url,
                error=f"Mock 不支持 $expand={expand!r}。请改用多次查询。",
            )

        try:
            df = self._load_data(service, entity_set)
        except FileNotFoundError as e:
            return ODataResponse(404, url, error=f"EntitySet 不存在或缺数据: {e}")

        try:
            if filter:
                df = apply_filter(df, filter)
            if apply:
                df = _apply_groupby_aggregate(df, apply)
            total = len(df)
            if orderby:
                df = apply_orderby(df, orderby)
            if skip:
                df = df.iloc[int(skip):].reset_index(drop=True)
            if top is not None:
                df = df.head(int(top))
            if select:
                df = apply_select(df, select)
        except FilterParseError as e:
            return ODataResponse(400, url, error=f"查询解析失败: {e}")
        except Exception as e:  # 兜底，转成业务错误而非崩溃
            return ODataResponse(500, url, error=f"查询执行失败: {e}")

        # 返回 $top 截断后的全部行(不再额外硬截断到 200)。"喂 LLM 只取少量样本"
        # 是调用方(Agent)的职责,不应在数据源层写死 —— 否则导出 Excel 也会被连带截断。
        rows = _df_to_rows(df)
        payload: dict[str, Any] = {
            "rows": rows,
            "row_count_returned": len(rows),
        }
        if count:
            payload["row_count_total"] = total
        return ODataResponse(status_code=200, url=url, json=payload)


# ============================== Helpers ==============================


def _build_mock_url(
    service: str,
    entity_set: str,
    filter: str | None,
    select: str | None,
    orderby: str | None,
    top: int | None,
    skip: int | None,
    expand: str | None,
    apply: str | None,
    count: bool,
) -> str:
    parts: list[str] = []
    if filter:
        parts.append(f"$filter={filter}")
    if select:
        parts.append(f"$select={select}")
    if orderby:
        parts.append(f"$orderby={orderby}")
    if top is not None:
        parts.append(f"$top={top}")
    if skip is not None:
        parts.append(f"$skip={skip}")
    if expand:
        parts.append(f"$expand={expand}")
    if apply:
        parts.append(f"$apply={apply}")
    if count:
        parts.append("$inlinecount=allpages")
    qs = "&".join(parts)
    return f"{_FAKE_BASE}/sap/opu/odata/sap/{service}/{entity_set}" + (f"?{qs}" if qs else "")


def _df_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → list[dict]，并把 NaN 转为 None。"""
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    out: list[dict[str, Any]] = []
    for r in records:
        clean = {}
        for k, v in r.items():
            if pd.isna(v):
                clean[k] = None
            elif hasattr(v, "item"):  # numpy scalar
                clean[k] = v.item()
            else:
                clean[k] = v
        out.append(clean)
    return out


def _apply_groupby_aggregate(df: pd.DataFrame, apply_expr: str) -> pd.DataFrame:
    """最小可用 $apply 实现：支持
        groupby((Col1,Col2),aggregate(KF1 with sum as KF1, KF2 with average as KF2_AVG))

    复杂表达式（filter/...）暂未支持，返回 raise 让上层报 400。
    """
    expr = apply_expr.strip()
    if not expr.startswith("groupby(("):
        raise FilterParseError(
            f"Mock 当前仅支持 groupby((...),aggregate(...)) 形式的 $apply,收到: {apply_expr}"
        )
    # 切出 groupby 字段
    try:
        gb_start = expr.index("((") + 2
        gb_end = expr.index("),", gb_start)
        gb_cols = [c.strip() for c in expr[gb_start:gb_end].split(",")]
        agg_start = expr.index("aggregate(", gb_end) + len("aggregate(")
        # 匹配 aggregate( 对应的右括号（栈嵌套,而非 rfind）
        depth = 1
        agg_end = -1
        for i in range(agg_start, len(expr)):
            ch = expr[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    agg_end = i
                    break
        if agg_end < 0:
            raise FilterParseError("aggregate( 括号未闭合")
        agg_body = expr[agg_start:agg_end]
    except ValueError as e:
        raise FilterParseError(f"$apply 语法解析失败: {e}") from e

    # 每个聚合项: "KF with sum as ALIAS"
    aggs: dict[str, tuple[str, str]] = {}
    for item in _split_top_level(agg_body, ","):
        item = item.strip()
        parts = item.split(" with ")
        if len(parts) != 2:
            raise FilterParseError(f"无法解析聚合项: {item}")
        col = parts[0].strip()
        right = parts[1].strip()
        as_split = right.split(" as ")
        func = as_split[0].strip()
        alias = as_split[1].strip() if len(as_split) > 1 else col
        aggs[alias] = (col, func)

    # 校验列存在
    missing = [c for c in gb_cols if c not in df.columns]
    missing += [col for _, (col, _) in aggs.items() if col not in df.columns]
    if missing:
        raise FilterParseError(f"$apply 字段不存在: {', '.join(missing)}")

    grouped = df.groupby(gb_cols, dropna=False)
    result_cols: dict[str, pd.Series] = {}
    for alias, (col, func) in aggs.items():
        func_lower = func.lower()
        if func_lower == "sum":
            result_cols[alias] = grouped[col].sum(numeric_only=True)
        elif func_lower in ("average", "avg"):
            result_cols[alias] = grouped[col].mean(numeric_only=True)
        elif func_lower == "min":
            result_cols[alias] = grouped[col].min(numeric_only=True)
        elif func_lower == "max":
            result_cols[alias] = grouped[col].max(numeric_only=True)
        elif func_lower in ("countdistinct", "count_distinct"):
            result_cols[alias] = grouped[col].nunique()
        elif func_lower == "count":
            result_cols[alias] = grouped[col].count()
        else:
            raise FilterParseError(f"不支持的聚合函数: {func}")

    result = pd.DataFrame(result_cols).reset_index()
    return result


def _split_top_level(s: str, sep: str) -> list[str]:
    """按分隔符切分,但跳过括号内的。"""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, c in enumerate(s):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts
