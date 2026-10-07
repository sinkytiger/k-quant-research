"""52주 신고가·신저가 (코스피·코스닥 보통주, KRX 일별매매 스냅샷).

- 수정가: krx_daily.adjust 와 같은 방식(전일대비로 분할 계수 f = (종가−대비)/전일종가)을 종목별로 한꺼번에 계산.
  창 안에서의 상대 비교만 하므로 창 시작 이전 이력은 필요 없다.
- 신고가: 그날 수정 고가 ≥ 직전 250거래일 수정 고가 최고치. 신저가: 수정 저가 ≤ 직전 250거래일 최저치.
  직전 창에 고가가 240일 이상 있어야 센다(상장 1년 미만·장기 거래정지 제외). 거래정지일(거래량 0)은 고가·저가를 비운다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LOOKBACK, MIN_OBS = 250, 240


def adjusted(tidy: pd.DataFrame) -> pd.DataFrame:
    """tidy long 표(Date, code, Open, High, Low, Close, Volume, Chg) → 수정 High·Low·Close 를 붙인 long 표."""
    d = tidy.sort_values(["code", "Date"]).drop_duplicates(["code", "Date"], keep="last").reset_index(drop=True)
    g = d.groupby("code", sort=False)
    prev = g["Close"].shift(1)
    base = d["Close"] - d["Chg"]
    f = (base / prev).where((prev > 0) & (base > 0), 1.0)
    f = f.where((f - 1).abs() > 1e-9, 1.0)
    gap = g["Date"].diff().dt.days
    f = f.where(~(gap > 60), 1.0)
    d["f"] = f.astype(float)
    # 수정가_t = 원시_t × Π_{s>t} f_s  (종목 안에서 뒤에서부터 누적곱, 한 칸 당김)
    rev = d.iloc[::-1]
    cp = rev.groupby("code", sort=False)["f"].cumprod().iloc[::-1]
    mult = cp.groupby(d["code"], sort=False).shift(-1).fillna(1.0)
    halted = (d["Volume"].fillna(0) <= 0) | (d["Open"].fillna(0) <= 0)
    d["aH"] = (d["High"] * mult).where(~halted)
    d["aL"] = (d["Low"] * mult).where(~halted)
    d["aC"] = d["Close"] * mult
    return d


def flags(high: pd.DataFrame, low: pd.DataFrame, lookback: int = LOOKBACK, min_obs: int = MIN_OBS):
    """날짜×종목 수정 고가·저가 → (신고가 bool, 신저가 bool, 직전 최고치, 직전 최저치)."""
    pmax = high.shift(1).rolling(lookback, min_periods=min_obs).max()
    pmin = low.shift(1).rolling(lookback, min_periods=min_obs).min()
    hi = (high >= pmax) & high.notna() & pmax.notna()
    lo = (low <= pmin) & low.notna() & pmin.notna()
    return hi, lo, pmax, pmin


def daily_counts(hi: pd.DataFrame, lo: pd.DataFrame, market: pd.Series, days: int = 250) -> dict:
    """시장별 일별 신고가·신저가 개수 (마지막 days 거래일)."""
    out = {"dates": [f"{x:%Y-%m-%d}" for x in hi.index[-days:]]}
    for mk in sorted(set(market.dropna())):
        cols = [c for c in hi.columns if market.get(c) == mk]
        out[mk] = {"hi": [int(v) for v in hi[cols].iloc[-days:].sum(axis=1)],
                   "lo": [int(v) for v in lo[cols].iloc[-days:].sum(axis=1)]}
    return out


def today_rows(kind: str, mask: pd.Series, d: pd.DataFrame, ref: pd.Series, close: pd.DataFrame, info: dict) -> list[dict]:
    """마지막 날 신고가(kind='hi') 또는 신저가 종목. ref = 직전 최고치/최저치."""
    last = close.index[-1]
    rows = []
    for c in mask[mask].index:
        cc = close[c].dropna()
        r1y = float(cc.iloc[-1] / cc.iloc[-251] - 1) if len(cc) > 250 else None
        prev = cc.iloc[-2] if len(cc) > 1 else np.nan
        x = d.at[c] if c in d.index else np.nan
        rows.append({"code": c, **info.get(c, {}), "close": float(cc.iloc[-1]),
                     "pct": float(cc.iloc[-1] / prev - 1) if prev and not np.isnan(prev) else None,
                     "beyond": float(x / ref[c] - 1) if ref.get(c) and not np.isnan(x) else None, "r1y": r1y,
                     "date": f"{last:%Y-%m-%d}"})
    return sorted(rows, key=lambda r: -(r.get("mcap") or 0))
