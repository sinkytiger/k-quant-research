"""실적 시즌 보드: 가장 최근 분기(회사 1,000곳 이상 보고)의 분기 실적과 전년 같은 분기 비교.

- 분기 값은 src/valuation.quarterly (누적 차이). 정기보고서 숫자라 잠정실적 공시보다 늦다.
- 흑자 전환 = 전년 같은 분기 영업이익 ≤ 0 → 이번 > 0, 적자 전환 = 반대.
- 데이터 오류로 보이는 종목(|분기 순이익| > 시총 5배)은 뺀다 (src/market_valuation.suspect 와 같은 기준).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_COMPANIES = 1000


def season_period(q: pd.DataFrame) -> int | None:
    n = q.reset_index().groupby("period")["stock_code"].nunique()
    ok = n[n >= MIN_COMPANIES]
    return int(ok.index.max()) if len(ok) else None


def label(period: int) -> str:
    return f"{(period - 1) // 4}년 {(period - 1) % 4 + 1}분기"


def season_rows(q: pd.DataFrame, mcap: dict[str, float], period: int | None = None) -> pd.DataFrame:
    """종목별 이번 분기·전년 같은 분기 매출·영업이익·순이익과 YoY, 전환 표시."""
    P = period or season_period(q)
    if P is None:
        return pd.DataFrame()
    cur = q.xs(P, level="period")
    prev = q.xs(P - 4, level="period") if (P - 4) in q.index.get_level_values("period") else pd.DataFrame()
    d = pd.DataFrame({"rev": cur["revenue"], "op": cur["op"], "ni": cur["ni"], "rcept_dt": cur["rcept_dt"]})
    d = d.join(prev[["revenue", "op", "ni"]].rename(columns={"revenue": "rev_p", "op": "op_p", "ni": "ni_p"}), how="left")
    d["mcap"] = pd.Series(mcap).reindex(d.index)
    bad = (d["ni"].abs() > 5 * d["mcap"]) | (d["ni_p"].abs() > 5 * d["mcap"])
    d = d[~bad.fillna(False)]
    yoy = lambda a, b: np.where((b > 0) & a.notna(), a / b - 1, np.nan)  # noqa: E731
    d["rev_yoy"], d["op_yoy"] = yoy(d["rev"], d["rev_p"]), yoy(d["op"], d["op_p"])
    d["turn"] = np.select([(d["op_p"] <= 0) & (d["op"] > 0), (d["op_p"] > 0) & (d["op"] <= 0), (d["op_p"] <= 0) & (d["op"] <= 0)],
                          ["흑자전환", "적자전환", "적자지속"], default="")
    d.attrs["period"] = P
    return d


def summary(d: pd.DataFrame, group: pd.Series | None = None) -> pd.DataFrame:
    """그룹(시장·업종)별 회사 수, 영업이익 증가 비율, 영업이익 합 YoY (둘 다 있는 회사끼리)."""
    both = d.dropna(subset=["op", "op_p"])
    g = both.groupby(group.reindex(both.index)) if group is not None else both.groupby(lambda _: "전체")
    out = g.agg(n=("op", "size"), up=("op", lambda s: float((s > both.loc[s.index, "op_p"]).mean())),
                op=("op", "sum"), op_p=("op_p", "sum"))
    out["op_sum_yoy"] = np.where(out["op_p"] > 0, out["op"] / out["op_p"] - 1, np.nan)
    return out
