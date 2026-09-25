"""수익률·IC 계산.

다중 시장(한·미) 합집합 패널에는 한쪽만 쉬는 날에 전 종목 NaN 행이 생긴다.
그 행을 둔 채 pct_change/shift 를 하면 휴장일 전후 수익률이 NaN 이 되어 사라진다.
실측: 삼성전자 2025-09~2026-08 +172%(버그) vs +220%(정상).
그래서 수익률 계산 전에 반드시 dropna(how="all").
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def daily_returns(close: pd.DataFrame) -> pd.DataFrame:
    c = close.dropna(how="all")
    return c.pct_change(fill_method=None)


def forward_returns(close: pd.DataFrame, h: int = 1) -> pd.DataFrame:
    """t 의 라벨 = t → t+h 수익률."""
    c = close.dropna(how="all")
    return c.shift(-h) / c - 1


def cross_sectional_ic(feature: pd.DataFrame, target: pd.DataFrame, min_names: int = 5) -> pd.Series:
    """날짜별 스피어만 순위상관."""
    f = feature.reindex_like(target)
    out = {}
    for d in target.index:
        a, b = f.loc[d], target.loc[d]
        ok = a.notna() & b.notna()
        if ok.sum() < min_names:
            continue
        ra, rb = a[ok].rank(), b[ok].rank()
        if ra.std() == 0 or rb.std() == 0:
            continue
        out[d] = float(np.corrcoef(ra, rb)[0, 1])
    return pd.Series(out, dtype=float)


def feature_ic(feature: pd.DataFrame, close: pd.DataFrame, h: int = 1,
               universe: pd.DataFrame | None = None, min_names: int = 5) -> pd.Series:
    fwd = forward_returns(close, h)
    if universe is not None:
        fwd = fwd.where(universe.reindex(index=fwd.index, columns=fwd.columns, fill_value=False).astype(bool))
    return cross_sectional_ic(feature, fwd, min_names=min_names)
