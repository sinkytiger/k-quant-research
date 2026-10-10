"""대차거래 (주식 빌려주기) 일별 — KIS 공식 API.

대차거래추이(일별) HHPST074500C0 (`/quotations/daily-loan-trans`): 한 번에 25거래일, START_DATE~END_DATE 안에서 최근부터.
  MRKT_DIV_CLS_CODE 1 = 코스피 전체, 2 = 코스닥 전체, 3 = 종목 (MKSC_SHRN_ISCD).
  저장 열: lend_new(체결 주수), lend_rdmp(상환 주수), lend_qty(잔고 주수), lend_amt(잔고 금액, 원 — API 는 백만원.
  검산: 삼성전자 2026-10-08 잔고 8,229만 주 × 종가 263,000 원 ≈ 21.6조 = 21,641,715 백만원).
대차잔고는 공매도 잔고의 대용치다 (공매도하려면 먼저 빌려야 한다). 다만 빌린 주식이 다 공매도되는 것은 아니다 (차익·헤지·결제용).
저장: raw/lend/<코드>.csv, 시장 전체는 raw/lend/_KOSPI.csv, _KOSDAQ.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

TR, PATH = "HHPST074500C0", "/uapi/domestic-stock/v1/quotations/daily-loan-trans"
MARKETS = {"_KOSPI": "1", "_KOSDAQ": "2"}
COLS = ["lend_new", "lend_rdmp", "lend_qty", "lend_amt"]


def _num(x):
    return pd.to_numeric(str(x if x is not None else "").replace(",", ""), errors="coerce")


def path(code: str) -> Path:
    return config.DATA / "raw" / "lend" / f"{code}.csv"


def parse(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("bsop_date", ""))
        if len(d) != 8 or not _num(r.get("stck_prpr")) > 0:
            continue
        out.append({"Date": pd.Timestamp(d), "lend_new": _num(r.get("new_stcn")), "lend_rdmp": _num(r.get("rdmp_stcn")),
                    "lend_qty": _num(r.get("rmnd_stcn")), "lend_amt": _num(r.get("rmnd_amt")) * 1_000_000})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame(columns=COLS)


def fetch(code: str, start, end=None, max_pages: int = 120) -> pd.DataFrame:
    """code 는 종목코드 또는 _KOSPI/_KOSDAQ. 끝 날짜를 당기며 25거래일씩."""
    from src import kis
    from src.data.flows import last_complete_day

    start = pd.Timestamp(start)
    cur = min(pd.Timestamp(end), last_complete_day()) if end is not None else last_complete_day()
    mk = MARKETS.get(code, "3")
    parts = []
    for _ in range(max_pages):
        if cur < start:
            break
        body, _ = kis.call(TR, PATH, {"MRKT_DIV_CLS_CODE": mk, "MKSC_SHRN_ISCD": "" if mk != "3" else code,
                                      "START_DATE": f"{start:%Y%m%d}", "END_DATE": f"{cur:%Y%m%d}", "CTS": ""})
        page = parse(list(body.get("output1") or []))
        if page.empty:
            break
        parts.append(page)
        first = page.index.min()
        if first <= start or first >= cur + pd.Timedelta(days=1):
            break
        cur = first - pd.Timedelta(days=1)
    if not parts:
        return pd.DataFrame(columns=COLS)
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out[out.index >= start]


def load(code: str) -> pd.DataFrame:
    p = path(code)
    return pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame(columns=COLS)


def save(code: str, new: pd.DataFrame) -> pd.DataFrame:
    old = load(code)
    df = pd.concat([old, new]) if len(old) else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    p = path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="Date")
    return df


def update_start(code: str, default_start) -> pd.Timestamp:
    d = load(code)
    return (d.index.max() - pd.Timedelta(days=7)) if len(d) else pd.Timestamp(default_start)
