"""DART 공시 이벤트 스터디 (사전 등록 dart_events_v1).

- 이벤트 추출: 정정공시 제외, 공백 제거 후 공시명 규칙, 접수일 시점 유니버스 종목, 20거래일 중복 제거.
- 진입: 접수일 **다음** 거래일 종가 (DART 목록에 시각이 없음).
- AR = 종목 일간수익률 − 벤치(KOSPI200 가격지수) 일간수익률, CAR_h = 진입 다음 날부터 h 일 AR 합.
- 창이 데이터·구간 밖으로 나가거나 창 안에 가격이 비면 그 이벤트는 뺀다(뺀 수를 보고).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from src.stats import nw_tstat

E1 = {"주요사항보고서(자기주식취득결정)", "주요사항보고서(자기주식취득신탁계약체결결정)"}
E2 = {"주요사항보고서(유상증자결정)"}
E3_PREFIX = "단일판매ㆍ공급계약체결"
E3_EXCLUDE = ("자회사", "종속회사", "해지")
DEDUP_DAYS = 20


def _nm(s: str) -> str:
    return re.sub(r"\s+", "", str(s))


def event_type(report_nm: str) -> str | None:
    s = _nm(report_nm)
    if s.startswith("["):
        return None
    if s in E1:
        return "E1_자사주취득"
    if s in E2:
        return "E2_유상증자"
    if s.startswith(E3_PREFIX) and not any(k in s for k in E3_EXCLUDE):
        return "E3_공급계약"
    return None


def extract(dart_df: pd.DataFrame, membership: dict, trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    """columns: event, code, rcept_dt, rcept_no, report_nm"""
    d = dart_df.copy()
    d["event"] = d["report_nm"].map(event_type)
    d = d[d["event"].notna() & (d["stock_code"].str.len() == 6)]
    snaps = sorted(membership)
    sidx = pd.DatetimeIndex(snaps)
    pos = sidx.searchsorted(pd.DatetimeIndex(d["rcept_dt"]), side="right") - 1
    d["in_uni"] = [p >= 0 and c in membership[snaps[p]] for p, c in zip(pos, d["stock_code"])]
    d = d[d["in_uni"]].rename(columns={"stock_code": "code"}).sort_values(["rcept_dt", "rcept_no"])
    td = pd.DatetimeIndex(trading_days)
    d["tpos"] = td.searchsorted(pd.DatetimeIndex(d["rcept_dt"]), side="left")
    keep, last = [], {}
    for r in d.itertuples():
        k = (r.event, r.code)
        if k in last and r.tpos - last[k] < DEDUP_DAYS:
            continue
        last[k] = r.tpos
        keep.append(r.Index)
    return d.loc[keep, ["event", "code", "rcept_dt", "rcept_no", "report_nm"]].reset_index(drop=True)


def car(events: pd.DataFrame, close: pd.DataFrame, bench: pd.Series, h: int,
        start=None, end=None, pre: int = 5) -> pd.DataFrame:
    """이벤트별 entry(진입일), car, pre_car. 창 끝이 end 를 넘거나 가격이 비면 제외."""
    close = close.dropna(how="all")
    td = close.index
    r = close.pct_change(fill_method=None)
    b = bench.reindex(td).pct_change(fill_method=None)
    ar = r.sub(b, axis=0)
    end_ts = pd.Timestamp(end) if end is not None else td[-1]
    out = []
    for e in events.itertuples():
        if start is not None and e.rcept_dt < pd.Timestamp(start):
            continue
        i = td.searchsorted(e.rcept_dt, side="right")  # 접수일 다음 거래일
        if i + h >= len(td) or td[i + h] > end_ts or e.code not in ar.columns or i < pre:
            continue
        win = ar[e.code].iloc[i + 1:i + h + 1]
        if win.isna().any():
            continue
        prew = ar[e.code].iloc[i - pre + 1:i + 1]
        out.append({"event": e.event, "code": e.code, "rcept_dt": e.rcept_dt, "entry": td[i],
                    "car": float(win.sum()), "pre_car": float(prew.sum()) if prew.notna().all() else np.nan})
    return pd.DataFrame(out)


def calendar_t(cars: pd.DataFrame) -> dict:
    """진입 월별 평균 CAR 의 평균과 NW t (lag 2)."""
    if cars.empty:
        return {"n_events": 0, "n_months": 0, "mean_car": np.nan, "nw_t": np.nan}
    m = cars.groupby(cars["entry"].dt.to_period("M"))["car"].mean()
    return {"n_events": int(len(cars)), "n_months": int(len(m)), "mean_car": float(m.mean()),
            "nw_t": nw_tstat(m, lags=2) if len(m) > 2 else np.nan,
            "event_mean": float(cars["car"].mean()), "event_median": float(cars["car"].median()),
            "pre_car_mean": float(cars["pre_car"].mean())}
