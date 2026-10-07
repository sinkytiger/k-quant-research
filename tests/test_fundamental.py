"""재무 팩터 시점 규칙: 접수일 다음 거래일부터, 15개월 넘은 재무 결측, 자본 ≤ 0 결측, qv 순위 평균."""
import numpy as np
import pandas as pd

from src.features import fundamental as fu


def _fin(code="000001", rcept=("2024-05-14", "2024-08-14", "2024-11-14", "2025-03-14", "2025-05-14"), eq=100.0):
    rows = []
    cum = {1: 10, 2: 20, 3: 30, 4: 40}
    for i, rd in enumerate(rcept):
        year, q = (2024, i + 1) if i < 4 else (2025, 1)
        for item, val in (("ni", cum[q]), ("revenue", cum[q] * 10), ("op", cum[q]), ("equity", eq), ("liab", 1.0), ("assets", 1.0)):
            rows.append({"year": year, "q": q, "stock_code": code, "fs": "CFS", "item": item, "value": float(val),
                         "rcept_dt": pd.Timestamp(rd)})
    return pd.DataFrame(rows)


def test_known_from_next_trading_day_and_ttm():
    days = pd.bdate_range("2025-03-10", "2025-06-30")
    mcap = pd.DataFrame(1000.0, index=days, columns=["000001"])
    f = fu.features(_fin(), mcap)
    ep = f["ep"]["000001"]
    # 2024 4분기(접수 2025-03-14 금) → 3/17(월)부터 TTM = 40
    assert np.isnan(ep.loc["2025-03-14"]) and ep.loc["2025-03-17"] == 40 / 1000
    # 2025 1분기(접수 5/14) → 5/15 부터 TTM = 2024Q2~Q4 (10×3) + 2025Q1 10 = 40
    assert ep.loc["2025-05-14"] == 40 / 1000 and ep.loc["2025-05-15"] == 40 / 1000
    assert f["bp"]["000001"].loc["2025-05-15"] == 100 / 1000


def test_stale_and_negative_equity():
    days = pd.bdate_range("2026-07-06", "2026-07-10")  # 마지막 분기 말 2025-03-31 에서 15개월(455일) 넘음
    mcap = pd.DataFrame(1000.0, index=days, columns=["000001"])
    assert fu.features(_fin(), mcap)["bp"].isna().all().all()
    days = pd.bdate_range("2025-05-15", "2025-05-20")
    mcap = pd.DataFrame(1000.0, index=days, columns=["000001"])
    assert fu.features(_fin(eq=-5.0), mcap)["bp"].isna().all().all()


def test_qv_rank_average():
    bp = pd.DataFrame({"A": [0.1], "B": [0.5], "C": [np.nan]})
    roe = pd.DataFrame({"A": [0.3], "B": [0.1], "C": [0.2]})
    q = fu.qv_from(bp, roe)
    assert q.loc[0, "A"] == q.loc[0, "B"] == 0.75 and np.isnan(q.loc[0, "C"])
