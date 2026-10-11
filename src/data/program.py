"""프로그램 매매 일별 — KIS 공식 API.

- 시장 전체: 프로그램매매 종합현황(일별) FHPPG04600001 (`/quotations/comp-program-trade-daily`), FID_MRKT_CLS_CODE K = 코스피, Q = 코스닥.
  기간 지정, 한 번에 25~31거래일. 금액 단위 백만원 → 원으로 저장.
  저장 열: arb_net(차익 순매수), nonarb_net(비차익 순매수), net(전체 순매수), buy, sell(전체 매수·매도 금액), net_rate(전체 순매수 ÷ 시장 거래대금 %).
  차익 = 선물과 현물 바스켓 가격 차이를 노리는 거래(만기일 근처에 몰린다), 비차익 = 바스켓을 한 번에 사고파는 거래(주로 외국인·기관의 지수형 매매).
- 종목: 종목별 프로그램매매추이(일별) FHPPG04650201 (`/quotations/program-trade-by-stock-daily`), FID_INPUT_DATE_1 까지 30거래일. 금액 단위 원.
  저장 열: net, buy, sell, value(그날 거래대금).
저장: raw/program/<코드>.csv, 시장은 raw/program/_KOSPI.csv, _KOSDAQ.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

MKT_TR, MKT_PATH = "FHPPG04600001", "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
STK_TR, STK_PATH = "FHPPG04650201", "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily"
MARKETS = {"_KOSPI": "K", "_KOSDAQ": "Q"}
MKT_COLS = ["arb_net", "nonarb_net", "net", "buy", "sell", "net_rate"]
STK_COLS = ["net", "buy", "sell", "value"]
MIL = 1_000_000


def _num(x):
    return pd.to_numeric(str(x if x is not None else "").replace(",", ""), errors="coerce")


def path(code: str) -> Path:
    return config.DATA / "raw" / "program" / f"{code}.csv"


def parse_market(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date", ""))
        if len(d) != 8:
            continue
        out.append({"Date": pd.Timestamp(d), "arb_net": _num(r.get("arbt_smtn_ntby_tr_pbmn")) * MIL,
                    "nonarb_net": _num(r.get("nabt_smtn_ntby_tr_pbmn")) * MIL, "net": _num(r.get("whol_smtn_ntby_tr_pbmn")) * MIL,
                    "buy": _num(r.get("whol_smtn_shnu_tr_pbmn")) * MIL, "sell": _num(r.get("whol_smtn_seln_tr_pbmn")) * MIL,
                    "net_rate": _num(r.get("whol_ntby_tr_pbmn_rate"))})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame(columns=MKT_COLS)


def parse_stock(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date", ""))
        if len(d) != 8 or not _num(r.get("stck_clpr")) > 0:
            continue
        out.append({"Date": pd.Timestamp(d), "net": _num(r.get("whol_smtn_ntby_tr_pbmn")), "buy": _num(r.get("whol_smtn_shnu_tr_pbmn")),
                    "sell": _num(r.get("whol_smtn_seln_tr_pbmn")), "value": _num(r.get("acml_tr_pbmn"))})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame(columns=STK_COLS)


def fetch(code: str, start, end=None, max_pages: int = 120) -> pd.DataFrame:
    """code 는 종목코드 또는 _KOSPI/_KOSDAQ. 끝 날짜를 당기며 받는다."""
    from src import kis
    from src.data.flows import last_complete_day

    start = pd.Timestamp(start)
    cur = min(pd.Timestamp(end), last_complete_day()) if end is not None else last_complete_day()
    mk = MARKETS.get(code)
    parts = []
    for _ in range(max_pages):
        if cur < start:
            break
        if mk:
            body, _ = kis.call(MKT_TR, MKT_PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_MRKT_CLS_CODE": mk,
                                                  "FID_INPUT_DATE_1": f"{start:%Y%m%d}", "FID_INPUT_DATE_2": f"{cur:%Y%m%d}"})
            page = parse_market(list(body.get("output") or []))
        else:
            body, _ = kis.call(STK_TR, STK_PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": f"{cur:%Y%m%d}"})
            page = parse_stock(list(body.get("output") or []))
        if page.empty:
            break
        parts.append(page)
        first = page.index.min()
        if first <= start or first >= cur + pd.Timedelta(days=1):
            break
        cur = first - pd.Timedelta(days=1)
    if not parts:
        return pd.DataFrame(columns=MKT_COLS if mk else STK_COLS)
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out[out.index >= start]


def load(code: str) -> pd.DataFrame:
    p = path(code)
    return pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame(columns=MKT_COLS if code in MARKETS else STK_COLS)


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


def expiry(year: int, month: int, calendar: pd.Series | None = None) -> pd.Timestamp:
    """코스피200 선물·옵션 만기일 = 그달 둘째 목요일. 그날이 휴장이면 앞 거래일 (KIS 달력에 있을 때만 확인)."""
    first = pd.Timestamp(year=year, month=month, day=1)
    d = first + pd.Timedelta(days=(3 - first.weekday()) % 7 + 7)
    if calendar is not None:
        while d in calendar.index and not bool(calendar[d]):
            d -= pd.Timedelta(days=1)
    return d


def next_expiries(today, calendar: pd.Series | None = None) -> dict:
    """다음 월물 옵션 만기일과 다음 동시만기일(3·6·9·12월, 선물 포함). 오늘이 만기일이면 오늘."""
    today = pd.Timestamp(today).normalize()
    out = {}
    y, m = today.year, today.month
    for _ in range(14):
        e = expiry(y, m, calendar)
        if e >= today:
            out.setdefault("monthly", e)
            if m % 3 == 0:
                out.setdefault("quarterly", e)
        if "quarterly" in out:
            break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
