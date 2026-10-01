"""시장 국면 지표: 변동성 연율화, 시장폭(거래정지 제외), 200일선 비율은 그날 유니버스만."""
import numpy as np
import pandas as pd
import pytest

from src import regime


def test_realized_vol_annualized():
    idx = pd.bdate_range("2024-01-01", periods=60)
    r = np.tile([0.01, -0.01], 30)
    close = pd.Series(100 * np.cumprod(1 + r), index=idx)
    v = regime.realized_vol(close, 20).iloc[-1]
    assert v == pytest.approx(pd.Series(r[-20:]).std() * np.sqrt(252), rel=1e-6)


def test_breadth_counts_and_excludes_halted():
    raw = pd.DataFrame({"BAS_DD": ["20240102"] * 5, "CMPPREVDD_PRC": ["10", "-5", "0", "3", "-1"],
                        "ACC_TRDVOL": ["100", "100", "100", "100", "0"]})  # 마지막은 거래정지
    b = regime.breadth(raw).iloc[0]
    assert (b["up"], b["down"]) == (2, 1) and b["ratio"] == pytest.approx(2 / 3)


def test_above_ma_uses_point_in_time_members():
    idx = pd.bdate_range("2024-01-01", periods=250)
    cols = [f"S{i}" for i in range(40)]
    close = pd.DataFrame(np.linspace(100, 200, 250)[:, None].repeat(40, 1), index=idx, columns=cols)  # 모두 상승 → 200일선 위
    close["S0"] = np.linspace(200, 100, 250)  # 하락
    mask = pd.DataFrame(True, index=idx, columns=cols)
    assert regime.above_ma(close, mask).iloc[-1] == pytest.approx(39 / 40)
    mask["S0"] = False  # 비편입이면 분모에서 빠진다
    assert regime.above_ma(close, mask).iloc[-1] == pytest.approx(1.0)


def test_percentile_of_last_value():
    assert regime.percentile(pd.Series([1, 2, 3, 4])) == 1.0
    assert regime.percentile(pd.Series([4, 3, 2, 1])) == 0.25
