"""패널 조립. 순서가 규칙이다: load_panel → restrict_universe → attach_labels.

시점별 편입과 유동성 필터를 라벨보다 먼저 적용해야
횡단면 순위 라벨이 그날 실제로 살 수 있었던 종목들 사이에서만 매겨진다.
"""
from __future__ import annotations

import pandas as pd

from src.features.ic import forward_returns
from src.universe import liquidity, prices
from src.universe import membership as members


def load_panel(codes: list[str] | None = None, total_return: bool = False) -> dict[str, pd.DataFrame]:
    """total_return=True 면 배당 포함 총수익 가격(src/universe/total_return.py).
    총수익 전략은 KODEX200(분배금 포함)과, 가격 전략은 KOSPI200 가격지수와 비교한다."""
    codes = codes if codes is not None else members.all_members()
    price = prices.panel(codes, "Close")
    if total_return:
        from src.universe.total_return import tr_panel

        close = tr_panel(codes).reindex_like(price)
    else:
        close = price
    volume = prices.panel(codes, "Volume").reindex_like(close)
    # 유동성 필터(거래대금 = 가격 × 거래량)는 price 로 계산한다. TR 가격은 누적 배당만큼 부풀어 있다.
    return {"close": close, "price": price, "volume": volume}


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
