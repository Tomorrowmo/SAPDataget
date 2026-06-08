"""LiveBWClient XML fallback parsing tests."""
from __future__ import annotations

from dataclasses import dataclass

from app.bw.live import LiveBWClient
from app.config import BWSettings


ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <m:count>2</m:count>
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:ReportID>ZRPT_SALES_OVERVIEW</d:ReportID>
        <d:ReportDescription>销售总览报表</d:ReportDescription>
      </m:properties>
    </content>
  </entry>
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:ReportID>ZRPT_MARGIN_ANALYSIS</d:ReportID>
        <d:ReportDescription>毛利分析报表</d:ReportDescription>
      </m:properties>
    </content>
  </entry>
</feed>
"""


@dataclass
class _FakeResponse:
    status_code: int = 200
    url: str = "http://bw.example/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet"
    text: str = ATOM_XML

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        raise ValueError("not json")


@dataclass
class _FakeJsonResponse:
    status_code: int = 200
    url: str = "http://bw.example"
    body: dict | None = None
    text: str = ""

    @property
    def ok(self) -> bool:
      return 200 <= self.status_code < 300

    def json(self):
      return self.body or {}


def _settings() -> BWSettings:
    from pathlib import Path

    return BWSettings(
        mode="live",
        mock_data_dir=Path("."),
        mock_latency_ms=0,
        base_url="http://bw.example",
        username="u",
        password="p",
        client="600",
        language="EN",
        verify_ssl=True,
        timeout=30,
        client_fallback=True,
        max_export_rows=50000,
    )


def test_execute_query_falls_back_to_atom_xml(monkeypatch):
    client = LiveBWClient(_settings())
    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: _FakeResponse())

    resp = client.execute_query("ZBW_QUERY_LIST_SRV", "LtResultSet", top=50, count=True)

    assert resp.ok, resp.error
    assert resp.json["row_count_total"] == "2"
    assert resp.json["row_count_returned"] == 2
    assert resp.json["rows"][0]["ReportID"] == "ZRPT_SALES_OVERVIEW"
    assert resp.json["rows"][0]["ReportDescription"] == "销售总览报表"


def test_get_retries_without_sap_client_on_401():
    client = LiveBWClient(_settings())
    calls: list[dict] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {})})
        if len(calls) == 1:
            return _FakeJsonResponse(status_code=401, url=url, body={"d": {"results": []}})
        return _FakeJsonResponse(
            status_code=200,
            url=url,
            body={"d": {"results": [{"TechnicalServiceName": "ZBW_QUERY_LIST_SRV"}]}}
        )

    client.session.get = fake_get  # type: ignore[assignment]
    resp = client.list_services(top=1)

    assert resp.ok, resp.error
    assert len(calls) == 2
    assert "sap-client" in calls[0]["params"]
    assert "sap-client" not in calls[1]["params"]


def test_execute_query_normalizes_value_array(monkeypatch):
    client = LiveBWClient(_settings())
    payload = {
        "@odata.count": "2",
        "value": [
            {"ReportID": "R1", "ReportDescription": "A"},
            {"ReportID": "R2", "ReportDescription": "B"},
        ],
    }
    monkeypatch.setattr(
        client,
        "_get",
        lambda *args, **kwargs: _FakeJsonResponse(status_code=200, body=payload),
    )

    resp = client.execute_query("ZBW_QUERY_LIST_SRV", "LtResultSet", top=20, count=True)

    assert resp.ok, resp.error
    assert resp.json["row_count_returned"] == 2
    assert resp.json["row_count_total"] == "2"
    assert resp.json["rows"][1]["ReportID"] == "R2"