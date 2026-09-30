"""DART 이벤트: 공시명 규칙, 유니버스·중복 제거, 진입은 접수 다음 거래일, CAR 계산."""
import numpy as np
import pandas as pd
import pytest

from src import events as ev


@pytest.mark.parametrize("nm,expect", [
    ("주요사항보고서(자기주식취득결정)", "E1_자사주취득"),
    ("주요사항보고서 (자기주식취득신탁계약체결결정)", "E1_자사주취득"),
    ("주요사항보고서(자기주식취득결정)(자회사의 주요경영사항)", None),
    ("[기재정정]주요사항보고서(자기주식취득결정)", None),
    ("주요사항보고서(자기주식취득신탁계약해지결정)", None),
    ("주요사항보고서(유상증자결정)", "E2_유상증자"),
    ("유상증자결정(종속회사의주요경영사항)", None),
    ("단일판매ㆍ공급계약체결", "E3_공급계약"),
    ("단일판매ㆍ공급계약체결(자율공시)", "E3_공급계약"),
    ("단일판매ㆍ공급계약체결(자회사의 주요경영사항)", None),
    ("단일판매ㆍ공급계약해지", None),
])
def test_event_rules(nm, expect):
    assert ev.event_type(nm) == expect


def test_extract_universe_and_dedup():
    td = pd.bdate_range("2024-01-01", periods=60)
    m = {pd.Timestamp("2024-01-01"): frozenset({"000010"})}
    d = pd.DataFrame({"report_nm": ["단일판매ㆍ공급계약체결"] * 3 + ["단일판매ㆍ공급계약체결"],
                      "stock_code": ["000010", "000010", "000010", "000020"],
                      "rcept_dt": pd.to_datetime(["2024-01-02", "2024-01-10", "2024-02-15", "2024-01-02"]),
                      "rcept_no": ["1", "2", "3", "4"]})
    out = ev.extract(d, m, td)
    assert list(out["rcept_no"]) == ["1", "3"]  # 20거래일 안 중복 제거, 비유니버스(000020) 제외


def test_car_entry_is_next_trading_day_and_sums_ar():
    td = pd.bdate_range("2024-01-01", periods=40)
    close = pd.DataFrame({"A": 100.0}, index=td)
    bench = pd.Series(100.0, index=td)
    i = td.get_loc(pd.Timestamp("2024-01-05"))  # 금요일 공시 → 진입 월요일(1/8)
    close.iloc[i:, 0] = 110.0      # 접수일 당일 급등: 진입 전이라 CAR 에 안 들어간다
    close.iloc[i + 2:, 0] = 121.0  # 진입 다음 날 +10%
    events = pd.DataFrame({"event": ["E1"], "code": ["A"], "rcept_dt": [pd.Timestamp("2024-01-05")]})
    out = ev.car(events, close, bench, h=5)
    assert out.loc[0, "entry"] == pd.Timestamp("2024-01-08")
    assert out.loc[0, "car"] == pytest.approx(0.10)
    assert out.loc[0, "pre_car"] == pytest.approx(0.10)  # 이미 움직인 부분은 pre 로 보고


def test_car_window_must_end_inside_period():
    td = pd.bdate_range("2024-01-01", periods=30)
    close = pd.DataFrame({"A": np.linspace(100, 130, 30)}, index=td)
    events = pd.DataFrame({"event": ["E1"], "code": ["A"], "rcept_dt": [td[20]]})
    assert ev.car(events, close, pd.Series(100.0, index=td), h=5, end=td[24]).empty
    assert len(ev.car(events, close, pd.Series(100.0, index=td), h=5, end=td[26])) == 1
