"""페이퍼 트래킹: NAV 계산, 월초 재조정, 게이트."""
import pandas as pd
import pytest

from src import paper
from src.backtest import CostModel

ZERO = CostModel(0, 0)


def test_single_asset_nav_is_price_ratio_minus_buy_cost():
    idx = pd.bdate_range("2026-09-28", periods=6)
    px = pd.Series([100, 101, 103, 102, 104, 110], index=idx, dtype=float)
    out = paper.nav({"X": 1.0}, "2026-09-28", {"X": px}, {"X": CostModel(buy=0.001, sell=0.001)})
    assert out.index[0] == idx[1]  # 시작일 다음 거래일 종가에 매수
    assert out["nav"].iloc[-1] == pytest.approx((110 / 101) * (1 - 0.001))


def test_monthly_rebalance_back_to_target():
    idx = pd.bdate_range("2026-01-28", "2026-02-05")
    a = pd.Series(100.0, index=idx)
    b = pd.Series(100.0, index=idx)
    b.loc["2026-01-30":] = 200.0  # 1월 말 B 두 배 → 비중 1/3 : 2/3
    out = paper.nav({"A": 0.5, "B": 0.5}, "2026-01-27", {"A": a, "B": b}, {"A": ZERO, "B": ZERO})
    assert out["nav"].loc["2026-01-30"] == pytest.approx(1.5)
    b.loc["2026-02-03":] = 400.0  # 2월 첫 거래일(2/2) 재조정 후 B 두 배 → 절반만 두 배
    out = paper.nav({"A": 0.5, "B": 0.5}, "2026-01-27", {"A": a, "B": b}, {"A": ZERO, "B": ZERO})
    assert out["nav"].loc["2026-02-03"] == pytest.approx(1.5 * 1.5)


def test_gate_counts_only_completed_months_and_requires_all_checks():
    idx = pd.bdate_range("2026-01-02", "2026-08-10")
    port = pd.DataFrame({"ret": 0.001}, index=idx)
    bench = pd.Series(0.0, index=idx)
    g = paper.gate(port, bench)
    assert g["months_complete"] == 7  # 1~7월 완료, 8월은 진행 중
    assert g["checks"]["months>=6"] and g["checks"]["cum>bench"]
    short = paper.gate(port.loc[:"2026-03-15"], bench)
    assert short["months_complete"] == 2 and not short["pass"]
