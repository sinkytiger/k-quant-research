"""시장 전체 PER·PBR: 시총 합 ÷ 이익 합, 데이터 오류 종목(이익 > 시총 5배) 제외, 이익 합 ≤ 0 이면 PER 결측."""
import numpy as np
import pandas as pd

from src import market_valuation as mv


def _caps(d):
    return pd.DataFrame({"Date": d, "code": ["A", "B", "C"], "market": "코스피", "mcap": [100.0, 100.0, 1.0]})


def test_aggregate_excludes_suspect_and_sums():
    d = pd.Timestamp("2026-10-06")
    ni = pd.DataFrame({"A": [10.0], "B": [-2.0], "C": [5000.0]}, index=[d])  # C: 이익이 시총의 5000배 → 오류로 제외
    eq = pd.DataFrame({"A": [100.0], "B": [50.0], "C": [1.0]}, index=[d])
    r = mv.aggregate(_caps(d), ni, eq).iloc[0]
    assert r["per"] == 200 / 8 and r["pbr"] == 200 / 150 and r["n"] == 2
    assert abs(r["cover"] - 200 / 201) < 1e-12


def test_aggregate_nonpositive_earnings_gives_nan_per():
    d = pd.Timestamp("2026-10-06")
    ni = pd.DataFrame({"A": [-1.0], "B": [-2.0], "C": [np.nan]}, index=[d])
    eq = pd.DataFrame({"A": [100.0], "B": [50.0], "C": [np.nan]}, index=[d])
    assert np.isnan(mv.aggregate(_caps(d), ni, eq).iloc[0]["per"])


def test_weekly_keeps_last_day():
    days = list(pd.bdate_range("2026-09-01", periods=12))
    w = mv.weekly(days, "2026-09-01")
    assert w[-1] == days[-1] and len(w) == 3
