"""시장 국면 지표 (대시보드 표시용, 매매 신호 아님).

- realized_vol: 일간수익률 window 일 표준편차 × √252
- breadth: 그날 오른 종목 ÷ (오른 + 내린) (보합·거래정지 제외), KRX 일별매매 스냅샷의 전일대비 부호
- above_ma: 시점별 유니버스 안에서 종가 > N일 이동평균 인 종목 비율 (그날 유니버스만)
- investor_flow: 시점별 유니버스 종목의 투자자별 순매수 합 (원)
- percentile: 오늘 값이 전체 이력에서 몇 % 위치인지 (0~1)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    r = close.dropna().pct_change(fill_method=None)
    return r.rolling(window, min_periods=window).std() * np.sqrt(252)


def breadth(raw: pd.DataFrame) -> pd.DataFrame:
    """raw: 일별매매 스냅샷 원본(BAS_DD, CMPPREVDD_PRC, ACC_TRDVOL). columns: up, down, ratio"""
    d = pd.DataFrame({"Date": pd.to_datetime(raw["BAS_DD"], format="%Y%m%d"),
                      "chg": pd.to_numeric(raw["CMPPREVDD_PRC"].astype(str).str.replace(",", ""), errors="coerce"),
                      "vol": pd.to_numeric(raw["ACC_TRDVOL"].astype(str).str.replace(",", ""), errors="coerce")})
    d = d[d["vol"] > 0]
    g = d.groupby("Date")["chg"]
    out = pd.DataFrame({"up": g.apply(lambda x: (x > 0).sum()), "down": g.apply(lambda x: (x < 0).sum())})
    out["ratio"] = out["up"] / (out["up"] + out["down"]).where(lambda x: x > 0)
    return out.sort_index()


def above_ma(close: pd.DataFrame, member_mask: pd.DataFrame, window: int = 200) -> pd.Series:
    ma = close.rolling(window, min_periods=int(window * 0.9)).mean()
    ok = member_mask.reindex(index=close.index, columns=close.columns, fill_value=False).astype(bool) & ma.notna()
    above = (close > ma) & ok
    n = ok.sum(axis=1)
    return (above.sum(axis=1) / n).where(n >= 30)


def investor_flow(flow: pd.DataFrame, member_mask: pd.DataFrame) -> pd.Series:
    m = member_mask.reindex(index=flow.index, columns=flow.columns, fill_value=False).astype(bool)
    return flow.where(m).sum(axis=1, min_count=1)


def percentile(s: pd.Series) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    return float((s <= s.iloc[-1]).mean())
