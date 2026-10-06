"""gs-quant 정의를 따른 시계열 함수와 급등 임계선.

gs-quant 와의 값 대조는 tests/test_gsq_reference.py (gs-quant 가 있는 환경에서만 돈다).
"""
import numpy as np
import pandas as pd
import pytest

from src.monitor import gsq, thresholds


def _px(n=300, seed=0, vol=0.02):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series(10_000 * np.exp(np.cumsum(rng.normal(0, vol, n))), index=idx)


# ---------- 성질 ----------
def test_volatility_is_annualized_percent():
    x = _px(vol=0.01)
    v = gsq.volatility(x, 252).iloc[-1]
    assert 12 < v < 20  # 일 1% ≈ 연 15.9%


def test_max_drawdown_known_path():
    x = pd.Series([100, 120, 90, 110, 60, 80.0], index=pd.bdate_range("2024-01-01", periods=6))
    assert gsq.max_drawdown(x).iloc[-1] == pytest.approx(60 / 120 - 1)
    assert (gsq.max_drawdown(x) <= 0).all()


def test_beta_of_self_is_one_and_of_double_is_two():
    x = _px()
    assert gsq.beta(x, x, 60).iloc[-1] == pytest.approx(1.0)
    y = x.iloc[0] * (1 + 2 * gsq.returns(x).fillna(0)).cumprod()
    assert gsq.beta(y, x, 60).iloc[-1] == pytest.approx(2.0)


def test_zscores_full_sample_standardized():
    z = gsq.zscores(_px())
    assert z.mean() == pytest.approx(0, abs=1e-12) and z.std(ddof=1) == pytest.approx(1)


# ---------- 임계선 ----------
def test_threshold_uses_only_past_sigma_and_bounds():
    x = _px(vol=0.03)
    thr = thresholds.threshold(x)
    assert thr.iloc[: thresholds.MIN_OBS].isna().all()
    ok = thr.dropna()
    assert (ok >= thresholds.FLOOR).all() and (ok <= thresholds.CAP).all()
    # 오늘 폭등이 오늘 임계선을 바꾸지 않는다
    y = x.copy()
    y.iloc[-1] = y.iloc[-2] * 1.25
    assert thresholds.threshold(y).iloc[-1] == pytest.approx(thr.iloc[-1])
    assert thresholds.moves(y)["surge"].iloc[-1]


def test_quiet_stock_hits_floor():
    thr = thresholds.threshold(_px(vol=0.002)).dropna()
    assert (thr == thresholds.FLOOR).all()

