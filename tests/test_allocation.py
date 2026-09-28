"""변동성 타게팅: 노출은 전날까지의 변동성으로만, 상한 1, 비용."""
import numpy as np
import pandas as pd
import pytest

from src import allocation as al
from src.backtest import CostModel


def _ret(n=80, seed=0, sd=0.01):
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(np.random.default_rng(seed).normal(0, sd, n), index=idx)


def test_exposure_uses_previous_day_volatility_only():
    r = _ret()
    t = r.index[50]
    base = al.exposure(r)
    r2 = r.copy()
    r2.loc[t] = -0.30  # 오늘 폭락
    after = al.exposure(r2)
    assert after.loc[t] == pytest.approx(base.loc[t])      # 오늘 노출은 안 바뀐다
    assert after.loc[r.index[51]] < base.loc[r.index[51]]  # 내일부터 줄어든다


def test_exposure_capped_at_one_and_scales_down_in_high_vol():
    calm, wild = _ret(sd=0.002), _ret(sd=0.05)
    assert al.exposure(calm).dropna().max() == pytest.approx(1.0)
    assert al.exposure(wild).dropna().mean() < 0.3


def test_full_exposure_matches_buy_and_hold_without_cost():
    r = _ret(sd=0.002)  # 변동성 낮아 늘 노출 1
    s, e = r.index[30], r.index[-1]
    out = al.voltarget(r, s, e, cost=CostModel(0, 0))
    bh = (1 + r.loc[r.index[31]:e]).prod() - 1  # 첫날(s) 종가 진입 → 다음 날부터 수익
    assert (1 + out["ret"]).prod() - 1 == pytest.approx(bh)
    assert out["ret"].iloc[0] == 0.0


def test_cost_charged_on_exposure_change():
    r = _ret(sd=0.05)
    out = al.voltarget(r, r.index[30], r.index[-1], cost=CostModel(0.001, 0.001))
    first = out.iloc[0]
    assert first["cost"] == pytest.approx(first["exposure"] * 0.001)  # 현금 → 첫 노출 매수
