"""페이퍼 트래킹: 등록된 포트폴리오의 일별 NAV 와 게이트 판정.

- 정의: paper/portfolios.json (git 에 커밋 → 언제부터 추적했는지가 이력으로 남는다).
- NAV 는 데이터로 매번 처음부터 다시 계산한다(결과 파일을 잃어도 되살릴 수 있다).
  규칙을 바꾸려면 기존 항목을 고치지 말고 새 이름으로 추가한다.
- 고정 비중 포트폴리오: 시작일 다음 거래일 종가에 매수, 매월 첫 거래일 종가에 목표 비중으로 재조정.
- 비용: ETF 는 증권거래세가 없다(매수·매도 0.05%). 주식은 kr_retail(0.065% / 0.265%).
- 수익 기준: ETF 수정가는 분배금 포함(총수익), 주식은 가격수익률. 벤치도 같은 종류끼리 비교한다.
- 게이트(기획서 7장): 완료 6개월 이상 AND 누적 순수익 > 벤치 AND 월 초과수익 t ≥ 1.96.
  통과해도 모의계좌까지만. 실주문은 하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src import config
from src.backtest import CostModel
from src.stats import nw_tstat
from src.universe import prices

PORTFOLIOS = config.ROOT / "paper" / "portfolios.json"
ETF_COST = CostModel(buy=0.0005, sell=0.0005)
STOCK_COST = CostModel()
GATE_MONTHS, GATE_T = 6, 1.96


def load_defs(path: Path = PORTFOLIOS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def series(code: str) -> pd.Series:
    """종가(수정). 벤치 파일(ETF·지수)을 먼저, 없으면 종목 파일."""
    df = prices.load_bench(code)
    if df.empty:
        df = prices.load(code)
    return df["Close"].dropna() if len(df) else pd.Series(dtype=float)


def cost_for(code: str) -> CostModel:
    return ETF_COST if not prices.load_bench(code).empty else STOCK_COST


def nav(weights: dict[str, float], start, closes: dict[str, pd.Series] | None = None,
        costs: dict[str, CostModel] | None = None) -> pd.DataFrame:
    """start 다음 거래일 종가 매수 → 이후 일별 NAV. 월초 재조정. 컬럼: nav, ret, cost."""
    closes = closes or {c: series(c) for c in weights}
    costs = costs or {c: cost_for(c) for c in weights}
    px = pd.DataFrame(closes).sort_index().dropna(how="all").ffill()
    px = px[px.index > pd.Timestamp(start)]
    if px.empty:
        return pd.DataFrame(columns=["nav", "ret", "cost"])
    tgt = pd.Series(weights, dtype=float)
    tgt = tgt / tgt.sum()
    rets = px.pct_change(fill_method=None).fillna(0.0)
    w = pd.Series(0.0, index=tgt.index)  # 첫날 전에는 전액 현금
    value, prev, rows = 1.0, 1.0, []
    month = None
    for i, d in enumerate(px.index):
        if w.sum() > 0:  # 보유 중이면 그날 수익, 비중은 가격 따라 흘러간다
            rr = rets.loc[d, w.index]
            pr = float((w * rr).sum())
            value *= 1 + pr
            if 1 + pr > 0:
                w = w * (1 + rr) / (1 + pr)
        c = 0.0
        if i == 0 or d.month != month:  # 첫날 종가 매수, 매월 첫 거래일 종가 재조정
            diff = tgt - w
            c = float(sum(max(v, 0) * costs[k].buy + max(-v, 0) * costs[k].sell for k, v in diff.items()))
            value *= 1 - c
            w = tgt.copy()
        month = d.month
        rows.append({"date": d, "nav": value, "ret": value / prev - 1, "cost": c})
        prev = value
    return pd.DataFrame(rows).set_index("date")


def gate(port: pd.DataFrame, bench_ret: pd.Series) -> dict:
    """완료된 달만 센다(이번 달은 미완료)."""
    if port.empty:
        return {"months_complete": 0, "pass": False, "reason": "기록 없음"}
    r = port["ret"]
    b = bench_ret.reindex(r.index).fillna(0.0)
    last = r.index.max()
    done = r[r.index < last.to_period("M").start_time]
    months = done.groupby(done.index.to_period("M")).apply(lambda x: (1 + x).prod() - 1)
    bm = b.loc[done.index].groupby(done.index.to_period("M")).apply(lambda x: (1 + x).prod() - 1)
    ex = months - bm.reindex(months.index).fillna(0.0)
    cum, bcum = float((1 + r).prod() - 1), float((1 + b).prod() - 1)
    t = nw_tstat(ex, lags=2) if len(ex) >= 3 else float("nan")
    checks = {"months>=6": len(months) >= GATE_MONTHS, "cum>bench": cum > bcum,
              "excess_t>=1.96": bool(t >= GATE_T) if t == t else False}
    return {"months_complete": int(len(months)), "cum_return": cum, "bench_cum_return": bcum,
            "monthly_excess_t": t, "checks": checks, "pass": all(checks.values()),
            "note": "통과해도 모의계좌까지만. 실주문 금지."}
