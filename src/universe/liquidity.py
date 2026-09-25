"""유동성 필터: 거래대금 롤링 20일 중앙값 하위 20% 제외.

shift(1) 필수. 오늘 거래대금이 컸다는 건 오늘 무슨 일이 있었다는 뜻이라
당일 값을 쓰면 미래를 본다.

파이프라인 순서: load_panel → restrict_universe → attach_labels (src/panel.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trading_value(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    return close * volume


def dollar_volume(close: pd.DataFrame, volume: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """t 시점 값 = t-1 까지의 거래대금 롤링 중앙값."""
    dv = trading_value(close, volume)
    return dv.rolling(window, min_periods=max(5, window // 2)).median().shift(1)


def restrict_universe(
    dv_med: pd.DataFrame,
    membership_mask: pd.DataFrame | None = None,
    drop_frac: float = 0.2,
    min_names: int = 30,
) -> pd.DataFrame:
    """True = 그날 후보. 라벨 붙이기 전에 적용해야 한다."""
    base = dv_med.notna()
    if membership_mask is not None:
        mm = membership_mask.reindex(index=base.index, columns=base.columns, fill_value=False)
        base &= mm.astype(bool)
    if drop_frac <= 0:
        return base
    ranks = dv_med.where(base).rank(axis=1, pct=True)
    keep = base & (ranks > drop_frac)
    too_few = keep.sum(axis=1) < min_names
    keep.loc[too_few] = base.loc[too_few]
    return keep


def membership_mask(index: pd.DatetimeIndex, columns, membership: dict) -> pd.DataFrame:
    """날짜별로 그 날짜 이전 가장 최근 스냅샷에 들어 있던 종목만 True."""
    columns = list(columns)
    out = np.zeros((len(index), len(columns)), dtype=bool)
    if membership:
        snaps = sorted(membership)
        per_snap = np.array([[c in membership[s] for c in columns] for s in snaps], dtype=bool)
        pos = pd.DatetimeIndex(snaps).searchsorted(pd.DatetimeIndex(index), side="right") - 1
        ok = pos >= 0
        out[ok] = per_snap[pos[ok]]
    return pd.DataFrame(out, index=index, columns=columns)
