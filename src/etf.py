"""ETF 성과·자금 흐름 (KRX Open API ETF 일별매매, 전 종목).

- 일간 수익률 = 종가 ÷ (종가 − 전일대비) − 1. 전일대비는 기준가 대비라 분배락·분할이 반영된다(분배금 포함 총수익).
- 자금 흐름(설정·환매) = (상장주식수_t − 상장주식수_{t−1}) × NAV_t. ETF 는 돈이 들어오면 새 주식이 설정된다.
  상장주식수가 하루에 절반 이하로 줄거나 두 배 넘게 늘면(분할·병합 추정) 그날 흐름은 0 으로 둔다.
- 분류는 종목명·기초지수명 규칙(아래 CATEGORY_RULES, 위에서부터 먼저 걸리는 쪽).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from src.universe import krx_daily

WINDOWS = {"1w": 5, "1m": 21, "3m": 63, "1y": 252}
NUM = {"TDD_CLSPRC": "close", "CMPPREVDD_PRC": "chg", "NAV": "nav", "MKTCAP": "aum",
       "LIST_SHRS": "shares", "ACC_TRDVAL": "value"}

CATEGORY_RULES = [
    ("레버리지·인버스", r"레버리지|인버스|2X|곱버스|\(H\)?선물인버스"),
    ("채권·금리", r"채권|국채|국고채|회사채|금리|머니마켓|MMF|CD|KOFR|단기채|통안채|은행채|크레딧|SOFR|단기자금"),
    ("원자재", r"금현물|골드|금선물|^.*\s금\b|은선물|실버|원유|WTI|구리|농산물|원자재|천연가스"),
    ("해외 주식", r"미국|S&P|나스닥|NASDAQ|다우|중국|차이나|홍콩|항셍|일본|니케이|인도|베트남|유로|유럽|글로벌|선진국|신흥국|"
                 r"대만|독일|MSCI|필라델피아|빅테크|테슬라|엔비디아|애플|마이크로소프트|구글|아마존|월드|해외|TOP10|라틴|브라질"),
    ("국내 주식", r".*"),
]


def category(name: str, index_name: str = "") -> str:
    text = f"{name} {index_name}"
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, text, re.I):
            return cat
    return "기타"


def load_tidy(start=None) -> pd.DataFrame:
    raw = krx_daily.load_snapshots("etf", start=start)
    if raw.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"Date": pd.to_datetime(raw["BAS_DD"], format="%Y%m%d"),
                       "code": raw["ISU_CD"].astype(str), "name": raw["ISU_NM"], "index_name": raw.get("IDX_IND_NM")})
    for src, dst in NUM.items():
        df[dst] = pd.to_numeric(raw[src].astype(str).str.replace(",", ""), errors="coerce")
    return df[df["close"] > 0].drop_duplicates(["Date", "code"], keep="last")


def panels(tidy: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """날짜×종목 패널: ret(일간 총수익), flow(원), aum, value, nav."""
    piv = {k: tidy.pivot(index="Date", columns="code", values=k).sort_index() for k in ("close", "chg", "nav", "aum", "shares", "value")}
    base = piv["close"] - piv["chg"]
    ret = (piv["close"] / base - 1).where(base > 0)
    ds = piv["shares"].diff()
    ratio = piv["shares"] / piv["shares"].shift(1)
    flow = (ds * piv["nav"]).where((ratio > 0.5) & (ratio < 2.0), 0.0)
    return {"ret": ret, "flow": flow, "aum": piv["aum"], "value": piv["value"], "nav": piv["nav"]}


def summary(tidy: pd.DataFrame) -> pd.DataFrame:
    """종목별 최신 요약: 기간 수익률, 연초 대비, 기간 자금 흐름, 순자산, 20일 평균 거래대금."""
    if tidy.empty:
        return pd.DataFrame()
    P = panels(tidy)
    ret, flow = P["ret"], P["flow"]
    last = ret.index.max()
    latest = tidy[tidy["Date"] == last].set_index("code")
    gross = (1 + ret.fillna(0))
    out = pd.DataFrame(index=latest.index)
    out["name"] = latest["name"]
    out["category"] = [category(n, i) for n, i in zip(latest["name"], latest["index_name"].fillna(""))]
    out["aum"] = latest["aum"]
    out["value_20d"] = P["value"].tail(20).mean()
    first = ret.notna().idxmax()  # 종목별 첫 거래일(스냅샷 안)
    for k, n in WINDOWS.items():
        if len(ret) <= n:
            out[f"ret_{k}"] = np.nan
            out[f"flow_{k}"] = np.nan
            continue
        start = ret.index[-n - 1]
        g = gross.iloc[-n:].prod() - 1
        ok = first.reindex(out.index) <= start
        out[f"ret_{k}"] = g.reindex(out.index).where(ok)
        out[f"flow_{k}"] = flow.iloc[-n:].sum().reindex(out.index).where(ok)
    ytd = ret.index[ret.index.year == last.year]
    if len(ytd):
        prev_end = ret.index[ret.index < ytd[0]]
        g = gross.loc[ytd].prod() - 1
        ok = first.reindex(out.index) <= (prev_end[-1] if len(prev_end) else ytd[0])
        out["ret_ytd"] = g.reindex(out.index).where(ok)
    out["flow_1m_pct"] = out["flow_1m"] / (out["aum"] - out["flow_1m"]).where(lambda x: x > 0)
    out.attrs["asof"] = last
    return out.reset_index().rename(columns={"index": "code"})
