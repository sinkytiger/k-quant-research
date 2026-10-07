"""52주 신고가·신저가: 분할 반영(수정가), 직전 창 대비 판정, 거래정지·짧은 이력 제외."""
import numpy as np
import pandas as pd

from src import highlow as hl


def _tidy(close, chg=None, vol=None, code="000001"):
    n = len(close)
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = np.asarray(close, dtype=float)
    chg = np.r_[0, np.diff(close)] if chg is None else np.asarray(chg, dtype=float)
    return pd.DataFrame({"Date": idx, "code": code, "Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": 100.0 if vol is None else vol, "Chg": chg})


def test_split_is_not_a_new_low():
    # 2:1 분할: 원시가가 100 → 50 으로 반 토막, 대비는 0(기준가 50 대비)
    close = [100.0] * 5 + [50.0] * 3
    chg = [0] * 5 + [0, 0, 0]
    d = hl.adjusted(_tidy(close, chg))
    assert np.allclose(d["aC"], 50.0)


def test_flags_need_full_window_and_skip_halts():
    idx = pd.bdate_range("2025-01-01", periods=10)
    H = pd.DataFrame({"A": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10.0], "B": [5.0] * 9 + [np.nan]})
    L = H.copy()
    hi, lo, pmax, pmin = hl.flags(H, L, lookback=5, min_obs=5)
    assert hi["A"].iloc[-1] and not hi["A"].iloc[3]  # 앞쪽은 창이 덜 차서 판정하지 않는다
    assert not hi["B"].iloc[-1] and not lo["B"].iloc[-1]  # 거래정지일은 판정하지 않는다
    assert hi["B"].iloc[-2]  # 같은 값(≥)도 신고가 — 보합 횡보 종목은 매일 걸린다
    assert lo["A"].sum() == 0


def test_daily_counts_by_market():
    idx = pd.bdate_range("2025-01-01", periods=3)
    hi = pd.DataFrame({"A": [True, False, True], "B": [True, True, False]}, index=idx)
    lo = ~hi
    c = hl.daily_counts(hi, lo, pd.Series({"A": "코스피", "B": "코스닥"}), days=2)
    assert c["코스피"]["hi"] == [0, 1] and c["코스닥"]["lo"] == [0, 1] and len(c["dates"]) == 2
