"""공매도·신용잔고 일별 (KIS 공식 API).

- 공매도 일별추이 FHPST04830000 (`/quotations/daily-short-sale`): 기간 지정, 한 번에 최대 100거래일 → 끝 날짜를 당기며 받는다.
  저장 열: short_qty(공매도 체결 수량), short_vol_pct(거래량 대비 %), short_amt(공매도 거래대금, 원), short_amt_pct(거래대금 대비 %).
  2023-11-06 ~ 2025-03-30 은 공매도 전면 금지 기간이라 0 이다.
- 신용잔고 일별추이 FHPST04760000 (`/quotations/daily-credit-balance`): 입력일 기준 30거래일씩 거꾸로.
  날짜 = 매매일(deal_date). 저장 열: loan_qty(융자 잔고 주수), loan_amt(융자 잔고 금액, 원 — API 는 만원. 검산: 삼성전자 2,153만 주 × 평균 매입가 약 22만 원 ≈ 4.7조 = 473,125,700 만원), loan_rate(잔고율 %, 상장주식 대비),
  loan_gvrt(공여율 %, 그날 거래 중 신용 매수 비중).
저장: raw/short/<코드>.csv, raw/credit/<코드>.csv (날짜로 병합)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

SHORT_TR, SHORT_PATH = "FHPST04830000", "/uapi/domestic-stock/v1/quotations/daily-short-sale"
CREDIT_TR, CREDIT_PATH = "FHPST04760000", "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
BAN = (pd.Timestamp("2023-11-06"), pd.Timestamp("2025-03-30"))  # 공매도 전면 금지


def _num(x):
    return pd.to_numeric(str(x).replace(",", ""), errors="coerce")


def path(kind: str, code: str) -> Path:
    return config.DATA / "raw" / kind / f"{code}.csv"


def parse_short(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date", ""))
        if len(d) != 8 or not _num(r.get("stck_clpr")) > 0:
            continue
        out.append({"Date": pd.Timestamp(d), "short_qty": _num(r.get("ssts_cntg_qty")), "short_vol_pct": _num(r.get("ssts_vol_rlim")),
                    "short_amt": _num(r.get("ssts_tr_pbmn")), "short_amt_pct": _num(r.get("ssts_tr_pbmn_rlim"))})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame()


def parse_credit(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("deal_date", ""))
        if len(d) != 8 or not _num(r.get("stck_prpr")) > 0:
            continue
        out.append({"Date": pd.Timestamp(d), "loan_qty": _num(r.get("whol_loan_rmnd_stcn")),
                    "loan_amt": _num(r.get("whol_loan_rmnd_amt")) * 10000, "loan_rate": _num(r.get("whol_loan_rmnd_rate")),
                    "loan_gvrt": _num(r.get("whol_loan_gvrt"))})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame()


def _last_day():
    from src.data.flows import last_complete_day
    return last_complete_day()


def fetch_short(code: str, start, end=None, max_pages: int = 40) -> pd.DataFrame:
    from src import kis

    start = pd.Timestamp(start)
    cur = min(pd.Timestamp(end), _last_day()) if end is not None else _last_day()
    parts = []
    for _ in range(max_pages):
        if cur < start:
            break
        body, _ = kis.call(SHORT_TR, SHORT_PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                                                  "FID_INPUT_DATE_1": f"{start:%Y%m%d}", "FID_INPUT_DATE_2": f"{cur:%Y%m%d}"})
        page = parse_short(list(body.get("output2") or []))
        if page.empty:
            break
        parts.append(page)
        first = page.index.min()
        if first <= start or len(page) < 100:
            break
        cur = first - pd.Timedelta(days=1)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="first")].sort_index()


def fetch_credit(code: str, start, end=None, max_pages: int = 120) -> pd.DataFrame:
    from src import kis

    start = pd.Timestamp(start)
    cur = min(pd.Timestamp(end), _last_day()) if end is not None else _last_day()
    parts = []
    for _ in range(max_pages):
        body, _ = kis.call(CREDIT_TR, CREDIT_PATH, {"fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20476",
                                                    "fid_input_iscd": code, "fid_input_date_1": f"{cur:%Y%m%d}"})
        page = parse_credit(list(body.get("output") or []))
        if page.empty:
            break
        parts.append(page)
        first = page.index.min()
        if first <= start or first >= cur:
            break
        cur = first - pd.Timedelta(days=1)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out[out.index >= start]


def save(kind: str, code: str, new: pd.DataFrame) -> pd.DataFrame:
    p = path(kind, code)
    old = pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame()
    df = pd.concat([old, new]) if len(old) else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="Date")
    return df


def load(kind: str, code: str) -> pd.DataFrame:
    p = path(kind, code)
    return pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame()


def update_start(kind: str, code: str, default_start) -> pd.Timestamp:
    p = path(kind, code)
    if not p.exists():
        return pd.Timestamp(default_start)
    old = pd.read_csv(p, index_col="Date", parse_dates=True)
    return (old.index.max() - pd.Timedelta(days=7)) if len(old) else pd.Timestamp(default_start)
