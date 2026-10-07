"""재무 가치·퀄리티 피처 (사전 등록 fin_factor_v1, docs/prereg/2026-10-07_fin_factors.md).

시점 규칙: 보고서 숫자는 접수일 **다음 거래일**부터 안다. 한 분기의 TTM·평균 자본은 그 분기 보고서 접수일에 안다.
마지막으로 아는 분기 말이 t 보다 15개월 넘게 지났으면 결측.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import valuation as v

STALE_DAYS = 455  # 15개월


def quarter_end(period: int) -> pd.Timestamp:
    y, q = (period - 1) // 4, (period - 1) % 4 + 1
    return pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)


def known_table(fin: pd.DataFrame) -> pd.DataFrame:
    """종목·분기별 (아는 날 기준) TTM 순이익·자본·평균 자본. 열: stock_code, period, rcept_dt, ni_ttm, equity, avg_eq."""
    q = v.quarterly(v.wide(fin))
    rows = []
    for code, g in q.groupby(level="stock_code"):
        g = g.droplevel("stock_code")
        for P in g.index:
            ni4 = [g["ni"].get(P - i) for i in range(4)]
            ni = float(sum(ni4)) if all(x is not None and x == x for x in ni4) else np.nan
            eq, eq0 = g["equity"].get(P), g["equity"].get(P - 4)
            avg = np.nanmean([eq, eq0]) if (eq is not None and eq == eq and eq > 0 and eq0 is not None and eq0 == eq0 and eq0 > 0) else np.nan
            rows.append({"stock_code": code, "period": int(P), "rcept_dt": g["rcept_dt"].get(P),
                         "ni_ttm": ni, "equity": eq if eq is not None else np.nan, "avg_eq": avg})
    return pd.DataFrame(rows)


def step_panels(kt: pd.DataFrame, days: pd.DatetimeIndex, codes: list[str]) -> dict[str, pd.DataFrame]:
    """아는 날(접수 다음 거래일)부터 다음 보고서 전까지 값을 이어 쓰는 날짜×종목 패널."""
    out = {k: pd.DataFrame(np.nan, index=days, columns=codes) for k in ("ni_ttm", "equity", "avg_eq", "qend")}
    kt = kt[kt["stock_code"].isin(codes) & kt["rcept_dt"].notna()].copy()
    # 접수일 다음 거래일 (접수일 당일은 모른다고 본다)
    pos = days.searchsorted(kt["rcept_dt"].values, side="right")
    kt = kt[pos < len(days)]
    kt["known"] = days[pos[pos < len(days)]]
    kt["qend"] = [quarter_end(p) for p in kt["period"]]
    for code, g in kt.sort_values(["known", "period"]).groupby("stock_code"):
        g = g.drop_duplicates("known", keep="last")
        g = g[g["period"] == g["period"].cummax()]  # 늦게 들어온 옛 분기 정정본이 최신 분기를 덮지 않게
        idx = g.set_index("known")
        for k in ("ni_ttm", "equity", "avg_eq"):
            out[k][code] = idx[k].reindex(days).ffill()
        out["qend"][code] = pd.Series(idx["qend"].astype("int64").astype(float), index=idx.index).reindex(days).ffill()
    # 오래된 재무는 쓰지 않는다
    age = (days.values.astype("datetime64[ns]").astype("int64")[:, None] - out["qend"].to_numpy()) / 86400e9
    stale = pd.DataFrame(age > STALE_DAYS, index=days, columns=codes) | out["qend"].isna()
    for k in ("ni_ttm", "equity", "avg_eq"):
        out[k] = out[k].mask(stale)
    return out


def features(fin: pd.DataFrame, mcap: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """bp, ep, roe (날짜×종목). qv 는 유니버스 마스크를 건 뒤 날짜별 순위로 만든다 (qv_from)."""
    sp = step_panels(known_table(fin), mcap.index, list(mcap.columns))
    cap = mcap.where(mcap > 0)
    eq = sp["equity"].where(sp["equity"] > 0)
    return {"bp": eq / cap, "ep": sp["ni_ttm"] / cap,
            "roe": sp["ni_ttm"] / sp["avg_eq"].where(sp["avg_eq"] > 0)}


def qv_from(bp: pd.DataFrame, roe: pd.DataFrame) -> pd.DataFrame:
    """그날 둘 다 있는 종목 안에서 bp 백분위 순위와 roe 백분위 순위의 평균."""
    both = bp.notna() & roe.notna()
    return (bp.where(both).rank(axis=1, pct=True) + roe.where(both).rank(axis=1, pct=True)) / 2
