"""누수 없는 변환들.

- winsorize 는 expanding(3σ). 전기간 평균·표준편차로 자르면 미래 분포를 본다.
- 급등 유지율은 급등일 + horizon 영업일 ≤ t 인(결과가 확정된) 이벤트만 쓴다.
"""
from __future__ import annotations

import pandas as pd


def winsorize_expanding(x: pd.DataFrame | pd.Series, n_sigma: float = 3.0, min_periods: int = 20):
    """t 시점 값을 t-1 까지의 expanding 평균 ± n_sigma·표준편차로 자른다."""
    mu = x.expanding(min_periods=min_periods).mean().shift(1)
    sd = x.expanding(min_periods=min_periods).std().shift(1)
    lo, hi = mu - n_sigma * sd, mu + n_sigma * sd
    out = x.clip(lower=lo, upper=hi)
    # 표본이 부족한 초기 구간은 자르지 않는다(기준이 없으므로)
    return out.where(mu.notna(), x)


def matured_events(event_dates, t, trading_days: pd.DatetimeIndex, horizon: int = 20) -> list[pd.Timestamp]:
    """t 시점에 결과(horizon 영업일 뒤 가격)가 이미 확정된 이벤트만.

    이벤트일 위치 + horizon ≤ t 의 위치 인 것만 통과한다.
    """
    trading_days = pd.DatetimeIndex(trading_days)
    t_pos = trading_days.searchsorted(pd.Timestamp(t), side="right") - 1
    out = []
    for d in event_dates:
        d = pd.Timestamp(d)
        pos = trading_days.searchsorted(d, side="left")
        if pos < len(trading_days) and trading_days[pos] == d and pos + horizon <= t_pos:
            out.append(d)
    return out
