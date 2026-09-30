"""현금배당 일정 — KIS 예탁원정보(배당일정) HHKDB669102C0.

- 종목당 한 번 호출로 기간 전체(2015~)의 기준일·주당 배당금(원, 당시 액면 기준)·구분(결산/분기/반기).
- 주당 배당금은 분할 전 금액이다 → 배당락 전날 **원시 종가**로 나눈 배당수익률로 쓴다(분할과 무관해진다).
- 저장: raw/kis_dividend/<code>.csv (record_date, dps, kind, pay_date)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

TR, PATH = "HHKDB669102C0", "/uapi/domestic-stock/v1/ksdinfo/dividend"
COLS = ["record_date", "dps", "kind", "pay_date"]


def div_path(code: str) -> Path:
    return config.DATA / "raw" / "kis_dividend" / f"{code}.csv"


def parse(rows: list[dict], code: str) -> pd.DataFrame:
    out = []
    for r in rows:
        if str(r.get("sht_cd", "")).strip() != code or str(r.get("stk_kind", "보통")).strip() not in ("보통", ""):
            continue
        dps = pd.to_numeric(str(r.get("per_sto_divi_amt", "")).replace(",", ""), errors="coerce")
        if pd.isna(dps) or dps <= 0:
            continue
        out.append({"record_date": pd.Timestamp(str(r["record_date"])), "dps": float(dps),
                    "kind": str(r.get("divi_kind", "")).strip(),
                    "pay_date": str(r.get("divi_pay_dt", "")).replace("/", "-").strip()})
    df = pd.DataFrame(out, columns=COLS)
    return df.drop_duplicates(["record_date", "dps"]).sort_values("record_date").reset_index(drop=True)


def fetch(code: str, start: str = "20150101", end: str | None = None) -> pd.DataFrame:
    from src import kis

    end = end or (pd.Timestamp.today() + pd.DateOffset(years=1)).strftime("%Y%m%d")
    body, _ = kis.call(TR, PATH, {"CTS": "", "GB1": "0", "F_DT": start, "T_DT": end, "SHT_CD": code, "HIGH_GB": ""})
    return parse(list(body.get("output1") or []), code)


def save(code: str, df: pd.DataFrame) -> None:
    p = div_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")


def load(code: str) -> pd.DataFrame:
    p = div_path(code)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(p, parse_dates=["record_date"], encoding="utf-8-sig")
