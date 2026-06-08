"""对接健壮性测试 —— 复现真实 SAP 行为(>200 行分页/全量、撇号转义、Edm 类型),
全部离线:mock 用 tmp 数据集,live 用合成的 V2 __next 分页响应。

要点:验证"代码中不含 mock 约束"——同一套 $filter 转义在 mock 解析器与真 SAP 都成立,
且 200 行硬截断已彻底移除(mock 与 live 都能拿全量)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app import odata
from app.bw.live import LiveBWClient
from app.bw.mock import MockBWClient
from app.config import BWSettings


# ============================== mock: tmp 数据集 ==============================

def _make_dataset(root: Path, n_rows: int) -> Path:
    """在 tmp 目录造一个 mock 服务 ZTEST_SRV/Items,含 n_rows 行 + 一行带撇号的客户名。"""
    svc = root / "services" / "ZTEST_SRV"
    (svc / "data").mkdir(parents=True, exist_ok=True)
    (root / "catalog.json").write_text(json.dumps({"services": [
        {"TechnicalServiceName": "ZTEST_SRV", "Title": "Test", "Description": "robustness"}
    ]}, ensure_ascii=False), encoding="utf-8")
    (svc / "meta.json").write_text(json.dumps({"entity_sets": [{
        "name": "Items", "keys": ["ID"], "properties": [
            {"name": "ID", "type": "Edm.String"},
            {"name": "CustomerName", "type": "Edm.String"},
            {"name": "Amount", "type": "Edm.Decimal"},
        ]}]}, ensure_ascii=False), encoding="utf-8")
    lines = ["ID,CustomerName,Amount"]
    for i in range(n_rows):
        # 第 7 行放一个带英文撇号的客户名,验证转义后端到端可查
        name = "O'Brien Trading" if i == 6 else f"Cust{i:04d}"
        lines.append(f"{i:05d},{name},{100 + i}")
    (svc / "data" / "Items.csv").write_text("\n".join(lines), encoding="utf-8")
    return root


def _mock(root: Path) -> MockBWClient:
    return MockBWClient(data_dir=root, latency_ms=0)


def test_mock_no_200_cap(tmp_path: Path):
    """移除 head(200) 后:top=1000 应拿到全部 250 行(旧行为被截到 200)。"""
    root = _make_dataset(tmp_path, 250)
    bw = _mock(root)
    resp = bw.execute_query("ZTEST_SRV", "Items", top=1000, count=True)
    assert resp.ok, resp.error
    assert resp.json["row_count_returned"] == 250, "不应再被硬截断到 200"
    assert resp.json["row_count_total"] == 250


def test_mock_top_and_skip(tmp_path: Path):
    root = _make_dataset(tmp_path, 250)
    bw = _mock(root)
    r1 = bw.execute_query("ZTEST_SRV", "Items", top=50, count=True)
    assert r1.json["row_count_returned"] == 50
    assert r1.json["row_count_total"] == 250
    r2 = bw.execute_query("ZTEST_SRV", "Items", orderby="ID", top=10, skip=100)
    assert r2.json["row_count_returned"] == 10
    assert r2.json["rows"][0]["ID"] == "00100"


def test_mock_apostrophe_filter_roundtrip(tmp_path: Path):
    """用 odata.build_filter 安全构造的撇号过滤,经 mock 的 OData 解析器应能命中 ——
    证明转义在"构造"与"解析"两侧自洽(与真 SAP 一致)。"""
    root = _make_dataset(tmp_path, 250)
    bw = _mock(root)
    flt = odata.build_filter(
        [{"field": "CustomerName", "op": "eq", "value": "O'Brien Trading"}],
        {"CustomerName": "Edm.String"},
    )
    assert flt == "CustomerName eq 'O''Brien Trading'"
    resp = bw.execute_query("ZTEST_SRV", "Items", filter=flt, count=True)
    assert resp.ok, resp.error
    assert resp.json["row_count_returned"] == 1
    assert resp.json["rows"][0]["CustomerName"] == "O'Brien Trading"


def test_mock_injection_filter_matches_nothing(tmp_path: Path):
    """注入串被转义成单个字面量后,作为整体匹配 → 命中 0 行(而非破出条件返回全表)。"""
    root = _make_dataset(tmp_path, 50)
    bw = _mock(root)
    flt = odata.build_filter(
        [{"field": "CustomerName", "op": "eq", "value": "X' or '1'='1"}],
        {"CustomerName": "Edm.String"},
    )
    resp = bw.execute_query("ZTEST_SRV", "Items", filter=flt, count=True)
    assert resp.ok, resp.error
    assert resp.json["row_count_returned"] == 0, "注入被中和,不应返回任何行"


# ============================== live: 合成 V2 __next 分页 ==============================

@dataclass
class _FakeJson:
    status_code: int = 200
    url: str = "http://bw.example"
    body: dict | None = None
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self.body is None:
            raise ValueError("not json")
        return self.body


def _live_settings() -> BWSettings:
    return BWSettings(
        mode="live", mock_data_dir=Path("."), mock_latency_ms=0,
        base_url="http://bw.example", username="u", password="p", client="600",
        language="EN", verify_ssl=True, timeout=30,
        client_fallback=True, max_export_rows=50000,
    )


def _page(ids, total=None, nxt=None):
    d = {"results": [{"KUNNR": str(i), "NETWR_F": str(i) + ".5"} for i in ids]}
    if total is not None:
        d["__count"] = str(total)
    if nxt is not None:
        d["__next"] = nxt
    return _FakeJson(body={"d": d})


def test_live_follows_v2_next_paging(monkeypatch):
    """live 跟随 d.__next 跨页合并到全量(250 行,3 页),修复'导出只拿一页/被截'。"""
    client = LiveBWClient(_live_settings())
    p1 = _page(range(0, 100), total=250, nxt="http://bw.example/next1")
    p2 = _page(range(100, 200), nxt="http://bw.example/next2")
    p3 = _page(range(200, 250))
    monkeypatch.setattr(client, "_get", lambda *a, **k: p1)
    pages = {"http://bw.example/next1": p2, "http://bw.example/next2": p3}
    monkeypatch.setattr(client, "_get_url", lambda url: pages[url])
    monkeypatch.setattr(client, "_cached_metadata", lambda svc: {})  # 跳过 EDMX

    resp = client.execute_query("ZBW_SALES_SRV", "SalesByCustomer", top=1000, count=True)
    assert resp.ok, resp.error
    assert resp.json["row_count_returned"] == 250
    assert resp.json["row_count_total"] == "250"
    assert resp.json["rows"][-1]["KUNNR"] == "249"


def test_live_paging_respects_target_top(monkeypatch):
    """target=top 时分页到够即停并截断到 top(120)。"""
    client = LiveBWClient(_live_settings())
    p1 = _page(range(0, 100), total=250, nxt="http://bw.example/n1")
    p2 = _page(range(100, 200), nxt="http://bw.example/n2")
    monkeypatch.setattr(client, "_get", lambda *a, **k: p1)
    monkeypatch.setattr(client, "_get_url", lambda url: p2)
    monkeypatch.setattr(client, "_cached_metadata", lambda svc: {})
    resp = client.execute_query("ZBW_SALES_SRV", "SalesByCustomer", top=120, count=True)
    assert resp.json["row_count_returned"] == 120


def test_live_coerces_edm_types(monkeypatch):
    """live 按 metadata 把 Decimal 字符串转 float(下游 Excel/统计才正确)。"""
    client = LiveBWClient(_live_settings())
    monkeypatch.setattr(client, "_get", lambda *a, **k: _page([1, 2], total=2))
    monkeypatch.setattr(client, "_cached_metadata",
                        lambda svc: {"entity_sets": [{"name": "SalesByCustomer",
                                     "properties": [{"name": "NETWR_F", "type": "Edm.Decimal"},
                                                    {"name": "KUNNR", "type": "Edm.String"}]}]})
    resp = client.execute_query("ZBW_SALES_SRV", "SalesByCustomer", top=10)
    rows = resp.json["rows"]
    assert rows[0]["NETWR_F"] == pytest.approx(1.5)
    assert isinstance(rows[0]["NETWR_F"], float)
    assert rows[0]["KUNNR"] == "1"          # ID 字符串保持不变


def test_live_parses_odata_error(monkeypatch):
    """4xx 时提取 SAP error.message.value,而非只回 'HTTP 400'。"""
    client = LiveBWClient(_live_settings())
    err = _FakeJson(status_code=400, body=None,
                    text='{"error":{"code":"X","message":{"value":"Property \'Regionx\' not found"}}}')
    err.body = None  # 走 text/error 路径

    class _Err(_FakeJson):
        @property
        def ok(self):
            return False
    e = _Err(status_code=400, text='{"error":{"message":{"value":"Property \'Regionx\' not found"}}}')
    # 让 r.headers 存在
    monkeypatch.setattr(client, "_get", lambda *a, **k: _with_headers(e))
    resp = client.execute_query("ZBW_SALES_SRV", "SalesByCustomer", top=10)
    assert not resp.ok
    assert resp.error == "Property 'Regionx' not found"


def _with_headers(obj):
    obj.headers = {"content-type": "application/json"}
    return obj
