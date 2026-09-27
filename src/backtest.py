"""롱온리 상위 분위 포트폴리오 백테스트 (일별, 비용 포함).

- 신호: t 종가 기준 점수. 체결: t+delay 종가 (기본 하루 지연). 새 비중은 체결일 다음 날 수익부터 적용.
- 리밸런싱 사이에는 비중이 가격에 따라 흘러간다(매일 재조정하지 않는다).
- 비용: 비대칭. 매수 cost_buy, 매도 cost_sell(증권거래세 포함) × 해당 방향 회전율. 체결일 수익에서 뺀다.
- 상폐: 가격이 끊긴 종목은 마지막 가격에 현금화(이후 수익 0)된 것으로 본다.
- 동일가중 기준선도 같은 엔진·같은 비용으로 돌린다(top_frac=1).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.features.ic import daily_returns
from src.stats import nw_tstat


@dataclass
class CostModel:
    buy: float = 0.00065   # 수수료+슬리피지
    sell: float = 0.00265  # 수수료+슬리피지+증권거래세 0.20%

    @property
    def round_trip(self) -> float:
        return self.buy + self.sell


KR_RETAIL = CostModel()


def eligible(close: pd.DataFrame, universe: pd.DataFrame, min_listing_days: int = 252) -> pd.DataFrame:
    """유니버스 안 + 상장(가격 이력) min_listing_days 이상."""
    seen = close.notna().cumsum()
    return universe.reindex(index=close.index, columns=close.columns, fill_value=False).astype(bool) & (seen >= min_listing_days)


def target_weights(score: pd.Series, ok: pd.Series, top_frac: float) -> pd.Series:
    s = score.where(ok).dropna()
    if s.empty:
        return pd.Series(dtype=float)
    n = max(1, int(np.ceil(len(s) * top_frac)))
    pick = s.sort_values(ascending=False).index[:n]
    return pd.Series(1.0 / n, index=pick)


def run(close: pd.DataFrame, score: pd.DataFrame, ok: pd.DataFrame, start, end,
        rebalance_every: int = 20, top_frac: float = 0.2, cost: CostModel = KR_RETAIL,
        delay: int = 1) -> pd.DataFrame:
    """일별 결과: ret(비용 차감), gross, cost, turnover, n_hold."""
    close = close.dropna(how="all")
    rets = daily_returns(close).fillna(0.0)  # 상폐 후 NaN → 0 (현금화)
    idx = close.index[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
    signal_days = set(idx[::rebalance_every])
    pos = {d: i for i, d in enumerate(close.index)}
    trades: dict[pd.Timestamp, pd.Series] = {}
    for t in signal_days:
        j = pos[t] + delay
        if j < len(close.index) and close.index[j] <= idx[-1]:
            trades[close.index[j]] = target_weights(score.loc[t], ok.loc[t], top_frac)

    w = pd.Series(dtype=float)
    out = []
    for d in idx:
        r = rets.loc[d]
        gross = float((w * r.reindex(w.index).fillna(0.0)).sum()) if len(w) else 0.0
        # 장중 가격 변화로 비중이 흘러간다
        if len(w):
            grown = w * (1 + r.reindex(w.index).fillna(0.0))
            total = grown.sum() + (1 - w.sum())  # 현금 몫 포함
            w = grown / total if total > 0 else grown
        c = turnover = 0.0
        if d in trades:  # 종가에 목표 비중으로 교체
            tgt = trades[d]
            allc = w.index.union(tgt.index)
            diff = tgt.reindex(allc).fillna(0.0) - w.reindex(allc).fillna(0.0)
            buys, sells = diff.clip(lower=0).sum(), (-diff).clip(lower=0).sum()
            c = buys * cost.buy + sells * cost.sell
            turnover = (buys + sells) / 2
            w = tgt.copy()
        out.append({"date": d, "gross": gross, "cost": c, "ret": gross - c,
                    "turnover": turnover, "n_hold": int((w > 0).sum())})
    return pd.DataFrame(out).set_index("date")


def stats(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if ret.empty:
        return {}
    eq = (1 + ret).cumprod()
    years = len(ret) / 252
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = ret.std() * np.sqrt(252)
    mdd = (eq / eq.cummax() - 1).min()
    return {"CAGR": cagr, "vol": vol, "sharpe": cagr / vol if vol > 0 else np.nan, "MDD": mdd,
            "total": eq.iloc[-1] - 1}


def excess_test(ret: pd.Series, base: pd.Series, block: int = 21) -> dict:
    """월(21거래일) 단위 초과수익의 평균과 NW t (lag 2). 행 단위가 아니라 날짜 블록 단위."""
    ex = (ret - base).dropna()
    m = ex.groupby(np.arange(len(ex)) // block).sum()
    return {"excess_ann": m.mean() * (252 / block), "excess_t": nw_tstat(m, lags=2), "months": len(m)}
