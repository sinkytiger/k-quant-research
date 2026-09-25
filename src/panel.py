"""패널 조립. 순서가 규칙이다: load_panel → restrict_universe → attach_labels.

시점별 편입과 유동성 필터를 라벨보다 먼저 적용해야
횡단면 순위 라벨이 그날 실제로 살 수 있었던 종목들 사이에서만 매겨진다.
"""
from __future__ import annotations

import pandas as pd

from src.features.ic import forward_returns
from src.universe import liquidity, prices
from src.universe import membership as members


def load_panel(codes: list[str] | None = None) -> dict[str, pd.DataFrame]:
    codes = codes if codes is not None else members.all_members()
    close = prices.panel(codes, "Close")
    volume = prices.panel(codes, "Volume").reindex_like(close)
    return {"close": close, "volume": volume}


def universe_mask(close: pd.DataFrame, volume: pd.DataFrame, membership: dict | None = None,
                  drop_frac: float = 0.2, min_names: int = 30) -> pd.DataFrame:
    m = members.load_membership() if membership is None else membership
    mm = liquidity.membership_mask(close.index, close.columns, m) if m else None
    dv = liquidity.dollar_volume(close, volume)
    return liquidity.restrict_universe(dv, mm, drop_frac=drop_frac, min_names=min_names)


def attach_labels(close: pd.DataFrame, universe: pd.DataFrame, h: int = 5,
                  kind: str = "cs_rank") -> pd.DataFrame:
    """universe 밖 칸은 NaN. cs_rank 는 universe 안에서만 순위를 매긴다."""
    fwd = forward_returns(close, h)
    fwd = fwd.where(universe.reindex(index=fwd.index, columns=fwd.columns, fill_value=False).astype(bool))
    if kind == "raw":
        return fwd
    if kind == "cs_rank":
        return fwd.rank(axis=1, pct=True)
    raise ValueError(kind)
