"""실적 시즌: 가장 최근 분기 선택(회사 수 기준), 전년 같은 분기 비교, 흑자/적자 전환, 데이터 오류 제외."""
import numpy as np
import pandas as pd

from src import earnings as e


def _q():
    idx = pd.MultiIndex.from_tuples([(c, p) for c in ("A", "B", "C") for p in (8102, 8106)], names=["stock_code", "period"])
    d = pd.DataFrame({"revenue": [100, 120, 50, 40, 10, 10], "op": [10, 20, -5, 3, 4, -1], "ni": [8, 15, -6, 2, 3, 99999],
                      "rcept_dt": pd.Timestamp("2026-08-14")}, index=idx)
    return d


def test_season_rows_and_turns(monkeypatch):
    monkeypatch.setattr(e, "MIN_COMPANIES", 2)
    d = e.season_rows(_q(), {"A": 1000.0, "B": 1000.0, "C": 1.0})
    assert d.attrs["period"] == 8106 and set(d.index) == {"A", "B"}  # C 는 순이익이 시총 5배 넘어 제외
    assert d.loc["A", "op_yoy"] == 1.0 and d.loc["B", "turn"] == "흑자전환" and np.isnan(d.loc["B", "op_yoy"])
    s = e.summary(d)
    assert s.iloc[0]["n"] == 2 and s.iloc[0]["op_sum_yoy"] == (23 / 5 - 1)
