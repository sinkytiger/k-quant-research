"""ETF: 분배금 포함 수익률, 설정·환매 흐름, 분할 날 흐름 제외, 분류."""
import pandas as pd
import pytest

from src import etf


def _tidy(rows):
    return pd.DataFrame(rows, columns=["Date", "code", "name", "index_name", "close", "chg", "nav", "aum", "shares", "value"])


def test_return_includes_distribution_and_flow_from_shares():
    d = pd.bdate_range("2026-09-01", periods=3)
    t = _tidy([
        (d[0], "A", "KODEX 200", "코스피 200", 100.0, 0.0, 100.0, 1e10, 1e8, 1e9),
        (d[1], "A", "KODEX 200", "코스피 200", 98.0, 0.0, 98.0, 1e10, 1e8, 1e9),     # 분배락: 기준가 98 → 대비 0
        (d[2], "A", "KODEX 200", "코스피 200", 99.0, 1.0, 99.0, 1e10, 1.1e8, 1e9),  # 1천만 주 설정
    ])
    P = etf.panels(t)
    assert P["ret"].loc[d[1], "A"] == pytest.approx(0.0)       # 분배락 하락은 수익이 아니다
    assert P["ret"].loc[d[2], "A"] == pytest.approx(99 / 98 - 1)
    assert P["flow"].loc[d[2], "A"] == pytest.approx(1e7 * 99)  # 늘어난 주식 × NAV


def test_split_day_flow_is_zero():
    d = pd.bdate_range("2026-09-01", periods=2)
    t = _tidy([(d[0], "B", "X", "", 10000.0, 0.0, 10000.0, 1e10, 1e6, 1e9),
               (d[1], "B", "X", "", 1000.0, 0.0, 1000.0, 1e10, 1e7, 1e9)])  # 10:1 분할
    assert etf.panels(t)["flow"].loc[d[1], "B"] == 0.0


@pytest.mark.parametrize("name,index_name,cat", [
    ("KODEX 200선물인버스2X", "코스피 200", "레버리지·인버스"),
    ("TIGER 미국S&P500", "S&P 500", "해외 주식"),
    ("KODEX CD금리액티브(합성)", "KIS CD금리", "채권·금리"),
    ("ACE KRX금현물", "KRX 금현물지수", "원자재"),
    ("KODEX 반도체", "KRX 반도체", "국내 주식"),
])
def test_category(name, index_name, cat):
    assert etf.category(name, index_name) == cat
