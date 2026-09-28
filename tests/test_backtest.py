"""백테스트 엔진: 체결 지연(룩어헤드 금지), 비대칭 비용, 상폐 처리."""
import numpy as np
import pandas as pd
import pytest

from src import backtest as bt


def _setup(n=10):
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.DataFrame({"A": 100.0, "B": 100.0}, index=idx)
    ok = pd.DataFrame(True, index=idx, columns=close.columns)
    score = pd.DataFrame({"A": 0.0, "B": 1.0}, index=idx)  # 늘 B 선택
    return idx, close, ok, score


def test_signal_day_and_trade_day_returns_are_not_captured():
    idx, close, ok, score = _setup()
    close.loc[idx[1]:, "B"] = 150.0   # 체결일(t+1) 당일 급등: 아직 못 산 상태
    close.loc[idx[2]:, "B"] = 300.0   # 체결 다음 날 급등: 보유 중
    res = bt.run(close, score, ok, idx[0], idx[-1], rebalance_every=100, top_frac=0.5, cost=bt.CostModel(0, 0))
    assert res.loc[idx[1], "gross"] == 0.0        # 신호 t=idx0, 체결 idx1 종가 → idx1 수익 없음
    assert res.loc[idx[2], "gross"] == pytest.approx(1.0)  # 150→300


def test_asymmetric_costs_on_turnover():
    idx, close, ok, score = _setup()
    cost = bt.CostModel(buy=0.001, sell=0.003)
    res = bt.run(close, score, ok, idx[0], idx[-1], rebalance_every=5, top_frac=0.5, cost=cost)
    assert res.loc[idx[1], "cost"] == pytest.approx(0.001)  # 현금 → B 전량 매수
    score2 = score.copy()
    score2.loc[idx[5]:, ["A", "B"]] = [1.0, 0.0]            # 두 번째 리밸런싱에서 B→A 교체
    res2 = bt.run(close, score2, ok, idx[0], idx[-1], rebalance_every=5, top_frac=0.5, cost=cost)
    assert res2.loc[idx[6], "cost"] == pytest.approx(0.001 + 0.003)
    assert res2.loc[idx[6], "turnover"] == pytest.approx(1.0)


def test_delisted_holding_goes_to_cash():
    idx, close, ok, score = _setup()
    close.loc[idx[4]:, "B"] = np.nan  # idx3 이 마지막 거래
    res = bt.run(close, score, ok, idx[0], idx[-1], rebalance_every=100, top_frac=0.5, cost=bt.CostModel(0, 0))
    assert res.loc[idx[4]:, "gross"].abs().sum() == 0.0


def test_min_listing_days_excludes_new_listings():
    idx = pd.bdate_range("2024-01-01", periods=300)
    close = pd.DataFrame({"OLD": 1.0, "NEW": np.nan}, index=idx)
    close.loc[idx[200]:, "NEW"] = 1.0
    uni = pd.DataFrame(True, index=idx, columns=close.columns)
    ok = bt.eligible(close, uni, min_listing_days=252)
    assert not ok["NEW"].any() and ok["OLD"].iloc[-1]


def test_cap_weighting_uses_signal_day_size():
    idx = pd.bdate_range("2024-01-01", periods=5)
    score = pd.Series({"A": 1.0, "B": 1.0, "C": 0.0})
    ok = pd.Series(True, index=score.index)
    w = bt.target_weights(score, ok, top_frac=2 / 3, size=pd.Series({"A": 300.0, "B": 100.0, "C": 1e9}))
    assert w.to_dict() == pytest.approx({"A": 0.75, "B": 0.25})  # C 는 제외돼서 시총이 커도 비중 0
