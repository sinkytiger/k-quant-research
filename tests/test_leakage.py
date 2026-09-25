"""룩어헤드 누수 규칙 (기획서 5장). 전부 실제로 밟았던 것들이다."""
import numpy as np
import pandas as pd
import pytest

from src.data.flows import normalized_flow
from src.features.ic import daily_returns, feature_ic, forward_returns
from src.features.transforms import matured_events, winsorize_expanding
from src.panel import attach_labels
from src.universe import liquidity, membership


def _days(n, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


def _panel(n=60, cols=("A", "B", "C"), seed=0):
    rng = np.random.default_rng(seed)
    idx = _days(n)
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (n, len(cols))), axis=0)),
                         index=idx, columns=list(cols))
    vol = pd.DataFrame(rng.integers(1_000, 5_000, (n, len(cols))).astype(float), index=idx, columns=list(cols))
    return close, vol


# ---------- 유동성 필터 ----------
def test_liquidity_filter_ignores_same_day_volume():
    close, vol = _panel()
    t = close.index[40]
    base = liquidity.dollar_volume(close, vol)
    spiked = vol.copy()
    spiked.loc[t:, "A"] *= 1_000  # t 부터 거래량 폭증
    after = liquidity.dollar_volume(close, spiked)
    assert after.loc[t, "A"] == pytest.approx(base.loc[t, "A"])  # 당일 값은 안 바뀐다
    later = close.index[51]  # 창 20일 중 과반이 폭증일이 되면 중앙값이 바뀐다
    assert after.loc[later, "A"] > base.loc[later, "A"] * 10


def test_restrict_universe_same_day_spike_does_not_change_membership():
    cols = [f"S{i}" for i in range(40)]
    close, vol = _panel(n=60, cols=cols)
    t = close.index[40]
    keep0 = liquidity.restrict_universe(liquidity.dollar_volume(close, vol), min_names=10)
    spiked = vol.copy()
    spiked.loc[t] *= np.linspace(0.01, 100, len(cols))
    keep1 = liquidity.restrict_universe(liquidity.dollar_volume(close, spiked), min_names=10)
    assert keep0.loc[t].equals(keep1.loc[t])


# ---------- 수급 ----------
def test_lag_hides_same_day_flow():
    close, vol = _panel()
    value = close * vol
    flow = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    t = close.index[40]
    base = normalized_flow(flow, value)
    f2 = flow.copy()
    f2.loc[t, "A"] = 1e12
    after = normalized_flow(f2, value)
    assert after.loc[t, "A"] == pytest.approx(base.loc[t, "A"])
    assert after.loc[close.index[41], "A"] > base.loc[close.index[41], "A"] * 1000


def test_normalizer_uses_past_volume_only():
    close, vol = _panel()
    value = close * vol
    flow = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    t = close.index[40]
    base = normalized_flow(flow, value)
    v2 = value.copy()
    v2.loc[t, "A"] *= 1_000_000  # 당일 거래대금만 폭증
    after = normalized_flow(flow, v2)
    assert after.loc[t, "A"] == pytest.approx(base.loc[t, "A"])


def test_normalization_removes_size_effect():
    rng = np.random.default_rng(1)
    idx = _days(80)
    small_value = pd.Series(rng.uniform(1e9, 2e9, len(idx)), index=idx)
    small_flow = pd.Series(rng.normal(0, 1e8, len(idx)), index=idx)
    scale = 5_000  # 삼성전자 vs 소형주
    value = pd.DataFrame({"BIG": small_value * scale, "SMALL": small_value})
    flow = pd.DataFrame({"BIG": small_flow * scale, "SMALL": small_flow})
    raw_rank_big_first = (flow["BIG"].abs() > flow["SMALL"].abs()).mean()
    assert raw_rank_big_first > 0.99  # 원화 그대로면 큰 종목이 늘 1등
    norm = normalized_flow(flow, value).dropna()
    np.testing.assert_allclose(norm["BIG"].to_numpy(), norm["SMALL"].to_numpy(), rtol=1e-9)


# ---------- winsorize ----------
def test_winsorize_is_expanding_not_full_sample():
    rng = np.random.default_rng(2)
    x = pd.Series(rng.normal(0, 1, 200), index=_days(200))
    before = winsorize_expanding(x)
    x2 = x.copy()
    x2.iloc[150:] = 1e6  # 미래에 극단값
    after = winsorize_expanding(x2)
    pd.testing.assert_series_equal(before.iloc[:150], after.iloc[:150])
    # 첫 극단값은 과거 기준 3σ 로 잘린다
    assert after.iloc[150] < 10


# ---------- 급등 유지율 ----------
def test_surge_events_only_after_horizon_matured():
    days = _days(100)
    events = [days[10], days[50], days[79], days[80]]
    t = days[99]
    got = matured_events(events, t, days, horizon=20)
    assert got == [days[10], days[50], days[79]]  # 80+20=100 > 99 → 제외
    assert matured_events([days[80]], days[100 - 1], days, horizon=19) == [days[80]]


# ---------- 휴장일 NaN 행 ----------
def test_feature_ic_ignores_all_nan_holiday_rows():
    close, _ = _panel(n=60, cols=[f"S{i}" for i in range(10)])
    true_total = close.iloc[-1] / close.iloc[0] - 1
    # 다른 시장만 연 날: 이 시장 종목 전부 NaN 인 행 삽입
    holidays = pd.DatetimeIndex([close.index[10] + pd.Timedelta(hours=12), close.index[30] + pd.Timedelta(hours=12)])
    union = pd.concat([close, pd.DataFrame(np.nan, index=holidays, columns=close.columns)]).sort_index()

    r = daily_returns(union)
    compounded = (1 + r.fillna(0)).prod() - 1
    np.testing.assert_allclose(compounded, true_total, rtol=1e-10)

    naive = (1 + union.pct_change(fill_method=None).fillna(0)).prod() - 1
    assert not np.allclose(naive, true_total)  # 버그 재현: dropna 없이는 수익률을 잃는다

    feat = union.pct_change(5, fill_method=None)
    ic = feature_ic(feat.reindex(union.index), union, h=1)
    assert not ic.index.isin(holidays).any()
    fwd = forward_returns(union, 1)
    assert not fwd.index.isin(holidays).any()


# ---------- 시점별 편입 → 라벨 ----------
def test_members_asof_never_sees_future_snapshot():
    m = {pd.Timestamp("2024-01-01"): frozenset({"A"}), pd.Timestamp("2024-02-01"): frozenset({"A", "B"})}
    assert membership.members_asof("2024-01-31", m) == frozenset({"A"})
    assert membership.members_asof("2024-02-01", m) == frozenset({"A", "B"})
    assert membership.members_asof("2023-12-31", m) == frozenset()


def test_membership_mask_matches_members_asof():
    m = {pd.Timestamp("2024-01-01"): frozenset({"A"}), pd.Timestamp("2024-02-01"): frozenset({"A", "B"})}
    idx = pd.bdate_range("2023-12-28", "2024-02-06")
    mask = liquidity.membership_mask(idx, ["A", "B", "C"], m)
    for d in idx:
        assert set(mask.columns[mask.loc[d]]) == set(membership.members_asof(d, m))


def test_labels_ranked_within_pointintime_universe():
    close, vol = _panel(n=40, cols=["A", "B", "C", "X"])
    close.loc[close.index[20]:, "X"] *= 10  # 비편입 종목 X 가 폭등
    universe = pd.DataFrame(True, index=close.index, columns=close.columns)
    universe["X"] = False
    labels = attach_labels(close, universe, h=5, kind="cs_rank")
    assert labels["X"].isna().all()
    without_x = attach_labels(close[["A", "B", "C"]], universe[["A", "B", "C"]], h=5, kind="cs_rank")
    pd.testing.assert_frame_equal(labels[["A", "B", "C"]], without_x)
