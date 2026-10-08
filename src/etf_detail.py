"""대시보드 ETF 상세: ETF 하나의 일봉·괴리율·자금 흐름·기초지수 추적 차이.

- 종가 선(1년)과 괴리율·자금 흐름. 괴리율 = 종가 ÷ NAV − 1 (장 마감 기준). 해외 지수 ETF 는 NAV 산정 시점 차이로 원래 크게 벌어진다.
- 자금 흐름 = 상장주식수 변화 × NAV (src.etf.panels 와 같은 정의).
- 추적 차이 = ETF 총수익(분배 포함) − 기초지수 수익률. 레버리지·인버스는 매일 재조정되어 기간 비교가 의미 없으므로 계산하지 않는다.
  기초지수가 가격지수면 분배금만큼 ETF 가 앞서 보이고, 해외 지수는 환율·시차가 섞인다.
"""
from __future__ import annotations

import math

import pandas as pd

NUM = {"TDD_CLSPRC": "close", "CMPPREVDD_PRC": "chg", "NAV": "nav", "TDD_OPNPRC": "open", "TDD_HGPRC": "high",
       "TDD_LWPRC": "low", "ACC_TRDVOL": "volume", "ACC_TRDVAL": "value", "MKTCAP": "aum", "LIST_SHRS": "shares",
       "OBJ_STKPRC_IDX": "idx"}


def tidy(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"Date": pd.to_datetime(raw["BAS_DD"].astype(str), format="%Y%m%d"),
                       "code": raw["ISU_CD"].astype(str), "name": raw["ISU_NM"], "index_name": raw.get("IDX_IND_NM")})
    for src, dst in NUM.items():
        df[dst] = pd.to_numeric(raw[src].astype(str).str.replace(",", ""), errors="coerce") if src in raw else float("nan")
    return df[df["close"] > 0].drop_duplicates(["Date", "code"], keep="last").sort_values(["code", "Date"])


def _r(x, d=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    x = round(x, d)
    return int(x) if x == int(x) and abs(x) < 1e15 else x  # 정수 가격은 소수점 없이 (파일 크기)


def one(g: pd.DataFrame, days: int = 250, leveraged: bool = False) -> dict:
    """한 ETF 의 화면용 묶음 (Date 오름차순 g). 추적 차이 1년(250일)을 재려면 g 에 days+1 행 이상이 있어야 한다."""
    if len(g) < 3:  # 막 상장한 ETF (전일 대비 계산에 이틀 이상 필요)
        return {}
    full = g
    g = g.tail(days + 1)
    base = g["close"] - g["chg"]
    ret = (g["close"] / base - 1).where(base > 0)
    ratio = g["shares"] / g["shares"].shift(1)
    flow = (g["shares"].diff() * g["nav"]).where((ratio > 0.5) & (ratio < 2.0), 0.0)
    prem = (g["close"] / g["nav"] - 1).where(g["nav"] > 0)
    g, ret, flow, prem = g.iloc[1:], ret.iloc[1:], flow.iloc[1:], prem.iloc[1:]

    fb = full["close"] - full["chg"]
    fret = (full["close"] / fb - 1).where(fb > 0)

    def track(n):
        idx = full["idx"]
        if leveraged or len(full) <= n or not (idx.iloc[-n - 1] == idx.iloc[-n - 1]) or not idx.iloc[-n - 1]:
            return None
        etf_ret = float((1 + fret.iloc[-n:].fillna(0)).prod() - 1)
        idx_ret = float(idx.iloc[-1] / idx.iloc[-n - 1] - 1)
        return {"etf": _r(etf_ret), "idx": _r(idx_ret), "diff": _r(etf_ret - idx_ret)}

    p20 = prem.tail(20)
    # 파일 크기 때문에 일봉 대신 종가만 (ETF 는 종가·괴리율이 핵심)
    return {"dates": [f"{d:%y%m%d}" for d in g["Date"]], "close": [_r(c, 2) for c in g["close"]],
            "prem": [_r(v, 4) for v in prem], "flow": [_r(v / 1e8, 1) for v in flow],
            "prem_now": _r(prem.iloc[-1], 5) if len(prem) else None,
            "prem_20d_abs": _r(p20.abs().mean(), 5) if len(p20) else None,
            "track_1y": track(250), "track_3m": track(63),
            "index_name": str(g["index_name"].iloc[-1] or ""), "idx_last": _r(g["idx"].iloc[-1], 2)}
