"""$filter parser 单元测试 —— 不依赖外部服务,纯逻辑。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.bw.odata_filter import (
    FilterParseError,
    apply_filter,
    apply_orderby,
    apply_select,
)


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame([
        {"Region": "HD", "Month": "202605", "Revenue": 1500, "Customer": "上海张江"},
        {"Region": "HD", "Month": "202604", "Revenue": 1200, "Customer": "苏州汽配"},
        {"Region": "HN", "Month": "202605", "Revenue": 800,  "Customer": "深圳华联"},
        {"Region": "HB", "Month": "202605", "Revenue": 950,  "Customer": "北京中科"},
        {"Region": "HD", "Month": "202605", "Revenue": 2100, "Customer": "宁波港务"},
    ])


# ============================== Filter ==============================

def test_eq_string(df):
    out = apply_filter(df, "Region eq 'HD'")
    assert len(out) == 3
    assert set(out["Region"]) == {"HD"}


def test_eq_number(df):
    out = apply_filter(df, "Revenue eq 1500")
    assert len(out) == 1
    assert out.iloc[0]["Customer"] == "上海张江"


def test_and(df):
    out = apply_filter(df, "Region eq 'HD' and Month eq '202605'")
    assert len(out) == 2
    assert set(out["Customer"]) == {"上海张江", "宁波港务"}


def test_or(df):
    out = apply_filter(df, "Region eq 'HN' or Region eq 'HB'")
    assert len(out) == 2


def test_gt(df):
    out = apply_filter(df, "Revenue gt 1000")
    assert len(out) == 3


def test_ge_le(df):
    out = apply_filter(df, "Revenue ge 950 and Revenue le 1500")
    assert len(out) == 3


def test_substringof(df):
    out = apply_filter(df, "substringof('张江', Customer)")
    assert len(out) == 1
    assert out.iloc[0]["Customer"] == "上海张江"


def test_startswith(df):
    out = apply_filter(df, "startswith(Customer, '上海')")
    assert len(out) == 1


def test_not(df):
    out = apply_filter(df, "not (Region eq 'HD')")
    assert len(out) == 2
    assert "HD" not in out["Region"].values


def test_parens(df):
    out = apply_filter(df, "(Region eq 'HD' or Region eq 'HN') and Revenue gt 1000")
    assert len(out) == 3


def test_unknown_column(df):
    with pytest.raises(FilterParseError):
        apply_filter(df, "NoSuchCol eq 'X'")


def test_empty_filter(df):
    out = apply_filter(df, "")
    assert len(out) == len(df)


def test_escaped_quote(df):
    df2 = df.copy()
    df2.loc[0, "Customer"] = "L'Oreal"
    out = apply_filter(df2, "Customer eq 'L''Oreal'")
    assert len(out) == 1


# ============================== Order ==============================

def test_orderby_desc(df):
    out = apply_orderby(df, "Revenue desc")
    assert list(out["Revenue"]) == [2100, 1500, 1200, 950, 800]


def test_orderby_multi(df):
    out = apply_orderby(df, "Region, Revenue desc")
    assert list(out["Region"]) == ["HB", "HD", "HD", "HD", "HN"]
    # HD 组内按 Revenue desc
    hd = out[out["Region"] == "HD"]
    assert list(hd["Revenue"]) == [2100, 1500, 1200]


# ============================== Select ==============================

def test_select(df):
    out = apply_select(df, "Region,Revenue")
    assert list(out.columns) == ["Region", "Revenue"]


def test_select_missing_column(df):
    with pytest.raises(FilterParseError):
        apply_select(df, "Region,NoSuchCol")
