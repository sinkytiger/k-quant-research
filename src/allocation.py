"""국면 배분: 변동성 타게팅 (기획서 6-4장).

노출 e_t = min(1, target / σ_window), σ = 일간수익률 표준편차 × √252, **shift(1)** — t 일 노출은
t-1 종가까지의 변동성으로 정한다. 매일 종가에 노출을 맞추고, 나머지는 현금(수익 cash_ret).
비용은 노출 변경분 × (매수 또는 매도 비용).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest import CostModel

ETF_COST = CostModel(buy=0.0005, sell=0.0005)


def exposure(ret: pd.Series, target: float = 0.15, window: int = 20) -> pd.Series:
    vol = ret.rolling(window, min_periods=window).std() * np.sqrt(252)
    e = (target / vol).clip(upper=1.0)
    return e.shift(1)  # 오늘 수익을 보고 오늘 노출을 정하지 않는다


def voltarget(ret: pd.Series, start, end, target: float = 0.15, window: int = 20,
              cost: CostModel = ETF_COST, cash_ret: float = 0.0) -> pd.DataFrame:
    """일별: ret(비용 차감), exposure, cost. 첫날은 그날 종가에 진입(수익 0)."""
    ret = ret.dropna()
    e_all = exposure(ret, target, window)
    days = ret.index[(ret.index >= pd.Timestamp(start)) & (ret.index <= pd.Timestamp(end))]
    w = 0.0  # 위험자산 비중 (첫날 전 현금)
    rows = []
    for d in days:
        r = ret.loc[d]
        pr = w * r + (1 - w) * cash_ret
        if 1 + pr > 0:
            w = w * (1 + r) / (1 + pr)  # 가격 변동으로 흘러간 비중
        tgt = e_all.loc[d]
        tgt = w if pd.isna(tgt) else float(tgt)
        diff = tgt - w
        c = diff * cost.buy if diff > 0 else -diff * cost.sell
        rows.append({"date": d, "ret": (1 + pr) * (1 - c) - 1, "exposure": tgt, "cost": c})
        w = tgt
    return pd.DataFrame(rows).set_index("date")
