"""MockBWClient 集成测试 —— 依赖 mock_data/。"""
from __future__ import annotations

import pytest

from app.bw.mock import MockBWClient


def test_list_services(mock_bw: MockBWClient):
    resp = mock_bw.list_services()
    assert resp.ok
    services = resp.json["services"]
    names = {s["TechnicalServiceName"] for s in services}
    assert {"ZBW_SALES_SRV", "ZBW_INV_SRV", "ZBW_FIN_SRV",
            "ZBW_PROD_SRV", "ZBW_PROC_SRV"}.issubset(names)


def test_list_services_search(mock_bw: MockBWClient):
    resp = mock_bw.list_services(search="销售")
    assert resp.ok
    services = resp.json["services"]
    assert len(services) >= 1
    assert all("销售" in (s.get("Title") or "") or "销售" in (s.get("Description") or "")
               for s in services)


def test_metadata(mock_bw: MockBWClient):
    resp = mock_bw.get_metadata("ZBW_SALES_SRV")
    assert resp.ok
    entity_sets = {e["name"] for e in resp.json["entity_sets"]}
    assert "SalesByOfficeView" in entity_sets
    assert "SalesByCustomer" in entity_sets


def test_metadata_unknown_service(mock_bw: MockBWClient):
    resp = mock_bw.get_metadata("ZBW_NOSUCH_SRV")
    assert not resp.ok
    assert resp.status_code == 404


def test_query_basic(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        top=5,
    )
    assert resp.ok
    rows = resp.json["rows"]
    assert 1 <= len(rows) <= 5
    assert "OfficeCode" in rows[0]
    assert "NETWR_F" in rows[0]


def test_query_with_filter(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        filter="Region eq 'HD' and CALMONTH eq '202605'",
        top=10,
    )
    assert resp.ok
    rows = resp.json["rows"]
    assert len(rows) >= 1
    for r in rows:
        assert r["Region"] == "HD"
        assert str(r["CALMONTH"]) == "202605"


def test_query_orderby_select(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        filter="CALMONTH eq '202605'",
        select="OfficeName,NETWR_F,Region",
        orderby="NETWR_F desc",
        top=3,
    )
    assert resp.ok
    rows = resp.json["rows"]
    assert len(rows) == 3
    assert list(rows[0].keys()) == ["OfficeName", "NETWR_F", "Region"]
    # 按 NETWR_F 降序
    revs = [r["NETWR_F"] for r in rows]
    assert revs == sorted(revs, reverse=True)


def test_query_count(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        filter="Region eq 'HD'",
        top=2,
        count=True,
    )
    assert resp.ok
    assert resp.json["row_count_total"] > resp.json["row_count_returned"]


def test_query_unknown_entityset(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="NoSuchEntity",
    )
    assert not resp.ok
    assert resp.status_code == 404


def test_query_invalid_filter(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        filter="BadCol eq 'X'",
    )
    assert not resp.ok
    assert resp.status_code == 400


def test_query_expand_rejected(mock_bw: MockBWClient):
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        expand="ToFoo",
    )
    assert not resp.ok
    assert "expand" in (resp.error or "").lower()


def test_apply_groupby_aggregate(mock_bw: MockBWClient):
    """$apply groupby + aggregate sum"""
    resp = mock_bw.execute_query(
        service="ZBW_SALES_SRV",
        entity_set="SalesByOfficeView",
        apply="groupby((Region),aggregate(NETWR_F with sum as NETWR_F))",
        top=10,
    )
    assert resp.ok, resp.error
    rows = resp.json["rows"]
    # 应该汇总到 6 个大区
    regions = {r["Region"] for r in rows}
    assert regions.issubset({"HD", "HN", "HB", "HX", "XB", "DB"})
    assert all("NETWR_F" in r for r in rows)
