"""DART 주요계정 → 분기 실적·TTM·밸류에이션 (PER·PBR·ROE·영업이익률·성장률).

- 연결(CFS)이 있으면 연결, 없으면 별도(OFS).
- 손익은 저장된 누적값의 차이로 분기 값을 만든다 (1분기 = 누적, 4분기 = 연간 − 3분기 누적). 앞 분기 누적이 없으면 비운다.
- TTM = 최근 4개 분기 합 (넷 다 있어야). 순이익은 비지배지분을 포함한 '당기순이익'이라 지배주주 기준 PER 과 조금 다르다.
- 시점: 화면은 오늘 기준 최신 보고서. 연구에 쓸 때는 asof 이전에 접수된(rcept_dt ≤ asof) 보고서만 쓴다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

IS_ITEMS = ("revenue", "op", "ni")
BS_ITEMS = ("assets", "liab", "equity")


def wide(fin: pd.DataFrame, asof=None) -> pd.DataFrame:
    """long → (stock_code, period) 행, 항목 열. period = 연도×4 + 분기. 연결 우선."""
    d = fin.copy()
    if asof is not None:
        d = d[d["rcept_dt"] <= pd.Timestamp(asof)]
    if d.empty:
        return pd.DataFrame()
    d["period"] = d["year"] * 4 + d["q"]
    has_cfs = d[d["fs"] == "CFS"].groupby(["stock_code", "period"]).size()
    d["use"] = [(s, p) in has_cfs.index if f == "CFS" else (s, p) not in has_cfs.index
                for s, p, f in zip(d["stock_code"], d["period"], d["fs"])]
    d = d[d["use"]]
    w = d.pivot_table(index=["stock_code", "period"], columns="item", values="value", aggfunc="first")
    w["rcept_dt"] = d.groupby(["stock_code", "period"])["rcept_dt"].max()
    for c in (*IS_ITEMS, *BS_ITEMS):
        if c not in w:
            w[c] = np.nan
    return w.sort_index()


def quarterly(w: pd.DataFrame) -> pd.DataFrame:
    """누적 손익 → 분기 손익 (같은 해 앞 분기 누적을 뺀다)."""
    q = w.copy()
    per = q.index.get_level_values("period")
    qn = (per - 1) % 4 + 1
    prev = w.groupby(level="stock_code")[list(IS_ITEMS)].shift(1)
    prev_per = pd.Series(per, index=q.index).groupby(level="stock_code").shift(1)
    contiguous = (prev_per == pd.Series(per, index=q.index) - 1)
    for c in IS_ITEMS:
        q[c] = np.where(qn == 1, w[c], np.where(contiguous, w[c] - prev[c], np.nan))
    return q


def metrics(q: pd.DataFrame, mcap: dict[str, float]) -> pd.DataFrame:
    """종목별 최신 분기 기준 지표."""
    rows = []
    for code, g in q.groupby(level="stock_code"):
        g = g.droplevel("stock_code")
        P = g.index.max()
        last4 = [P - i for i in range(4)]
        prev4 = [P - 4 - i for i in range(4)]

        def ttm(c, ps):
            v = [g[c].get(p) for p in ps]
            return float(sum(v)) if all(x is not None and x == x for x in v) else None

        ni, op, rev = ttm("ni", last4), ttm("op", last4), ttm("revenue", last4)
        ni_prev, op_prev = ttm("ni", prev4), ttm("op", prev4)
        eq = g["equity"].get(P)
        eq_prev = g["equity"].get(P - 4)
        cap = mcap.get(code)
        suspect = bool(cap and ((ni is not None and abs(ni) > 5 * cap) or (eq == eq and eq is not None and eq > 50 * cap)))
        if suspect:
            ni = op = rev = eq = eq_prev = None  # 데이터 오류로 보이는 값 (예: 단위가 어긋난 공시) — 지표를 만들지 않는다
        avg_eq = np.nanmean([x for x in (eq, eq_prev) if x is not None and x == x]) if (eq == eq and eq is not None) else None

        def yoy(c):
            a, b = g[c].get(P), g[c].get(P - 4)
            return float(a / b - 1) if a is not None and b is not None and a == a and b == b and b > 0 else None

        rows.append({
            "code": code, "period": int(P), "label": f"{str((P - 1) // 4)[2:]}.{(P - 1) % 4 + 1}Q",
            "rcept_dt": g["rcept_dt"].get(P), "mcap": cap,
            "rev_ttm": rev, "op_ttm": op, "ni_ttm": ni, "equity": eq if eq == eq else None,
            "per": cap / ni if cap and ni and ni > 0 else None,
            "pbr": cap / eq if cap and eq and eq == eq and eq > 0 else None,
            "roe": ni / avg_eq if ni is not None and avg_eq and avg_eq > 0 else None,
            "opm": op / rev if op is not None and rev and rev > 0 else None,
            "debt": (g["liab"].get(P) / eq) if eq and eq == eq and eq > 0 and g["liab"].get(P) == g["liab"].get(P) else None,
            "rev_yoy": yoy("revenue"), "op_yoy": yoy("op"),
            "ni_ttm_chg": (ni / ni_prev - 1) if ni is not None and ni_prev and ni_prev > 0 else None,
            "op_ttm_chg": (op / op_prev - 1) if op is not None and op_prev and op_prev > 0 else None,
            "loss": ni is not None and ni <= 0, "suspect": suspect,
        })
    return pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()


def history(q: pd.DataFrame, code: str, n: int = 8) -> list[list]:
    """종목 최근 n 분기 [라벨, 매출, 영업이익, 순이익] (원)."""
    if code not in q.index.get_level_values("stock_code"):
        return []
    g = q.xs(code, level="stock_code").tail(n)
    out = []
    for p, r in g.iterrows():
        lab = f"{str((p - 1) // 4)[2:]}.{(p - 1) % 4 + 1}Q"
        out.append([lab] + [None if (r[c] != r[c]) else float(r[c]) for c in IS_ITEMS])
    return out
