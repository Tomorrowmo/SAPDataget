"""app/odata.py 单测 —— 转义/安全 filter/Edm 类型转换/错误体解析。

用"真实 SAP 编码形状"的离线输入断言(Decimal 字符串、/Date(ms)/、OData 错误体),
最大化对 live 行为的可信度(接真 SAP 仍须按 §8.8 复核)。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app import odata


# ---------- 转义 ----------
def test_quote_doubles_single_quote():
    assert odata.odata_quote("O'Brien") == "'O''Brien'"
    assert odata.odata_quote("HD") == "'HD'"


def test_quote_neutralizes_injection():
    # 注入串被收进单个转义字面量,而非破出引号
    q = odata.odata_quote("HD' or '1'='1")
    assert q == "'HD'' or ''1''=''1'"


# ---------- edm_literal ----------
def test_edm_literal_by_type():
    assert odata.edm_literal("HD", "Edm.String") == "'HD'"
    assert odata.edm_literal("1221.4", "Edm.Decimal") == "1221.4"
    assert odata.edm_literal(10, "Edm.Int32") == "10"
    assert odata.edm_literal("2026-05-01", "Edm.DateTime") == "datetime'2026-05-01T00:00:00'"
    assert odata.edm_literal(True, "Edm.Boolean") == "true"
    # 非法数值降级为转义字符串,避免拼出非法 $filter
    assert odata.edm_literal("x' or 1=1", "Edm.Decimal") == "'x'' or 1=1'"
    # 未知类型从严当字符串转义
    assert odata.edm_literal("a'b", None) == "'a''b'"


# ---------- build_filter ----------
def test_build_filter_basic_and():
    types = {"CALMONTH": "Edm.String", "Region": "Edm.String"}
    f = odata.build_filter(
        [{"field": "CALMONTH", "op": "eq", "value": "202605"},
         {"field": "Region", "op": "eq", "value": "HD"}],
        types,
    )
    assert f == "CALMONTH eq '202605' and Region eq 'HD'"


def test_build_filter_neutralizes_injection():
    f = odata.build_filter([{"field": "Region", "op": "eq", "value": "HD' or '1'='1"}],
                           {"Region": "Edm.String"})
    assert f == "Region eq 'HD'' or ''1''=''1'"


def test_build_filter_functions_and_datetime():
    f = odata.build_filter([{"field": "CustomerName", "op": "contains", "value": "上海"}], {})
    assert f == "substringof('上海',CustomerName)"
    f2 = odata.build_filter([{"field": "ERDAT", "op": "ge", "value": "2026-05-01"}],
                            {"ERDAT": "Edm.DateTime"})
    assert f2 == "ERDAT ge datetime'2026-05-01T00:00:00'"


def test_build_filter_rejects_bad_field_and_op():
    with pytest.raises(ValueError):
        odata.build_filter([{"field": "Region or 1=1", "op": "eq", "value": "x"}], {})
    with pytest.raises(ValueError):
        odata.build_filter([{"field": "Region", "op": "like", "value": "x"}], {})


# ---------- Edm 类型转换(真实 SAP 编码) ----------
def test_coerce_decimal_and_int_from_string():
    assert odata.coerce_edm_value("1221.4", "Edm.Decimal") == pytest.approx(1221.4)
    assert isinstance(odata.coerce_edm_value("1221.4", "Edm.Decimal"), float)
    assert odata.coerce_edm_value("42", "Edm.Int32") == 42
    assert isinstance(odata.coerce_edm_value("42", "Edm.Int32"), int)


def test_coerce_v2_date_ms():
    v = odata.coerce_edm_value("/Date(1714521600000)/", "Edm.DateTime")
    assert isinstance(v, dt.datetime)
    assert v.year == 2024 and v.month == 5 and v.day == 1


def test_coerce_string_id_stays_string():
    # CALMONTH/KUNNR 这类 ID 字符串不能被转成数字
    assert odata.coerce_edm_value("202605", "Edm.String") == "202605"
    assert odata.coerce_edm_value("0001234", "Edm.String") == "0001234"


def test_coerce_unparseable_passthrough():
    assert odata.coerce_edm_value("N/A", "Edm.Decimal") == "N/A"


def test_coerce_rows_and_prop_types():
    meta = {"entity_sets": [{"name": "X", "properties": [
        {"name": "A", "type": "Edm.Decimal"}, {"name": "B", "type": "Edm.String"}]}]}
    types = odata.prop_types(meta, "X")
    assert types == {"A": "Edm.Decimal", "B": "Edm.String"}
    rows = odata.coerce_rows([{"A": "3.5", "B": "202605"}], types)
    assert rows == [{"A": 3.5, "B": "202605"}]


# ---------- SAP 错误体解析 ----------
def test_parse_error_json_v2():
    body = '{"error":{"code":"SY/530","message":{"lang":"en","value":"Property \'Regionx\' not found"}}}'
    assert odata.parse_odata_error(body, "application/json") == "Property 'Regionx' not found"


def test_parse_error_json_message_string():
    body = '{"error":{"message":"bad request"}}'
    assert odata.parse_odata_error(body) == "bad request"


def test_parse_error_xml():
    body = ('<?xml version="1.0"?><error xmlns="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
            '<code>005</code><message xml:lang="en">Filter expression invalid</message></error>')
    assert odata.parse_odata_error(body, "application/xml") == "Filter expression invalid"


def test_parse_error_none_for_garbage():
    assert odata.parse_odata_error("just some text") is None
    assert odata.parse_odata_error("") is None
