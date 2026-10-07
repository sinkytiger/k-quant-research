"""시장 전체 PER·PBR (코스피·코스닥 보통주, 주 단위, 시점 맞춤).

- PER = 그날 시가총액 합 ÷ 그날까지 아는 TTM 순이익 합 (적자 포함, 같은 종목 집합). 이익 합이 0 이하면 결측.
- PBR = 시가총액 합 ÷ 자본총계 합, ROE = 순이익 합 ÷ 자본 합 (모두 재무를 아는 종목끼리).
- 재무를 아는 날·오래된 재무 규칙은 fin_factor_v1 과 같다 (src/features/fundamental.step_panels).
- 커버리지 = 재무를 아는 종목의 시총 ÷ 전체 보통주 시총.
- 데이터 오류로 보이는 종목(suspect)은 뺀다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.universe import krx_daily, membership


def weekly(days: list[pd.Timestamp], start, step: int = 5) -> list[pd.Timestamp]:
    d = [x for x in days if x >= pd.Timestamp(start)]
    return d[::-1][::step][::-1]  # 마지막 날을 꼭 넣는다


def caps_on(days: list[pd.Timestamp]) -> pd.DataFrame:
    """고른 날짜들의 보통주 시가총액 long 표 (Date, code, market, mcap). 이전상장 종목은 그날 실제 시장으로."""
    parts = []
    for ds, mk in (("stk", "코스피"), ("ksq", "코스닥")):
        have = set(krx_daily.saved_days(ds))
        for d in days:
            if d not in have:
                continue
            r = pd.read_csv(krx_daily.snap_path(ds, d), dtype=str, encoding="utf-8-sig", usecols=["ISU_CD", "ISU_NM", "MKTCAP"])
            r = r[[membership.is_common_stock(c, n) for c, n in zip(r["ISU_CD"], r["ISU_NM"])]]
            parts.append(pd.DataFrame({"Date": d, "code": r["ISU_CD"].astype(str), "market": mk,
                                       "mcap": pd.to_numeric(r["MKTCAP"].str.replace(",", ""), errors="coerce")}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["Date", "code", "market", "mcap"])


def suspect(ni, eq, mcap):
    """데이터 오류로 보이는 값: |TTM 순이익| > 시총 5배, 또는 자본 > 시총 50배 (예: 단위가 어긋난 공시).
    자회사 이익을 비지배지분까지 합쳐 시총보다 이익이 큰 지주사(2~3배)는 남긴다."""
    return (ni.abs() > 5 * mcap) | (eq > 50 * mcap)


def aggregate(caps: pd.DataFrame, ni: pd.DataFrame, eq: pd.DataFrame) -> pd.DataFrame:
    """caps(long) + 날짜×종목 TTM 순이익·자본 → 시장·날짜별 PER·PBR·ROE·커버리지·이익 합."""
    long = pd.concat({"ni": ni.stack(), "eq": eq.stack()}, axis=1)
    long.index.names = ["Date", "code"]
    c = caps.merge(long.reset_index(), on=["Date", "code"], how="left")
    c = c[c["mcap"] > 0]
    bad = suspect(c["ni"], c["eq"], c["mcap"])
    c = c.assign(ni=c["ni"].mask(bad), eq=c["eq"].mask(bad))
    rows = []
    for (d, mk), g in c.groupby(["Date", "market"]):
        both = g.dropna(subset=["ni", "eq"])
        E, B, M = both["ni"].sum(), both["eq"].sum(), both["mcap"].sum()
        rows.append({"Date": d, "market": mk, "per": M / E if E > 0 else np.nan, "pbr": M / B if B > 0 else np.nan,
                     "roe": E / B if B > 0 else np.nan, "cover": M / g["mcap"].sum(), "earn": E, "mcap": M, "n": len(both)})
    return pd.DataFrame(rows)
