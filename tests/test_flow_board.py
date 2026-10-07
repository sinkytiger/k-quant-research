"""수급 탭 계산: 기간 합계, 연속 순매수(부호·0·결측에서 끊김), 기간 수익률."""
import numpy as np
import pandas as pd

from src import flow_board as fb


def test_window_sums():
    idx = pd.bdate_range("2026-01-01", periods=30)
    p = pd.DataFrame({"A": 1.0, "B": -2.0}, index=idx)
    s = fb.window_sums(p, (1, 5, 20))
    assert s.at["A", 5] == 5 and s.at["B", 20] == -40 and s.at["A", 1] == 1


def test_streak_sign_and_breaks():
    assert fb.streak([1, -1, 2, 3, 4]) == 3
    assert fb.streak([5, -1, -2]) == -2
    assert fb.streak([1, 2, 0]) == 0
    assert fb.streak([1, np.nan, 2, 3]) == 2
    assert fb.streak([]) == 0


def test_returns_window():
    idx = pd.bdate_range("2026-01-01", periods=10)
    c = pd.DataFrame({"A": np.arange(100, 110, dtype=float)}, index=idx)
    r = fb.returns(c, idx[-1], (1, 5))
    assert abs(r.at["A", 1] - (109 / 108 - 1)) < 1e-12 and abs(r.at["A", 5] - (109 / 104 - 1)) < 1e-12
