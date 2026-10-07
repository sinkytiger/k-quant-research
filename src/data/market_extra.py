"""홈 화면용 시장 데이터 (KIS 공식 API).

- 해외 지수·환율 일봉: FHKST03030100 (`/overseas-price/v1/quotations/inquire-daily-chartprice`).
  FID_COND_MRKT_DIV_CODE N = 해외지수, X = 환율. 한 번에 기간을 지정해 받는다.
- 시장별 투자자 일별: FHPTJ04040000 (`/quotations/inquire-investor-daily-by-market`).
  한 번 호출에 약 300거래일. 금액 단위 백만원 → 원으로 저장.
- 국내 휴장일: CTCA0903R (`/quotations/chk-holiday`). 기준일부터 약 24일의 개장 여부. KIS 안내대로 하루 1번만 부른다.
저장: raw/global/<name>.csv, raw/market_investor/<KOSPI|KOSDAQ>.csv, raw/kis_calendar.csv (날짜로 병합)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

GLOBAL = {  # 이름: (시장구분, 코드, 표시 이름)
    "NASDAQ": ("N", "COMP", "나스닥"),
    "SPX": ("N", "SPX", "S&P 500"),
    "VIX": ("N", "VIX", "VIX"),
    "USDKRW": ("X", "FX@KRW", "원/달러"),
}
MARKETS = {"KOSPI": ("0001", "KSP"), "KOSDAQ": ("1001", "KSQ")}
UNIT = 1_000_000


def _num(x):
    return pd.to_numeric(str(x).replace(",", ""), errors="coerce")


def global_path(name: str) -> Path:
    return config.DATA / "raw" / "global" / f"{name}.csv"


def investor_path(mkt: str) -> Path:
    return config.DATA / "raw" / "market_investor" / f"{mkt}.csv"


def _merge_save(p: Path, new: pd.DataFrame) -> pd.DataFrame:
    old = pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame()
    df = pd.concat([old, new]) if len(old) else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="Date")
    return df


def parse_global(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date", ""))
        c = _num(r.get("ovrs_nmix_prpr"))
        if len(d) == 8 and pd.notna(c) and c > 0:
            out.append({"Date": pd.Timestamp(d), "Open": _num(r.get("ovrs_nmix_oprc")), "High": _num(r.get("ovrs_nmix_hgpr")),
                        "Low": _num(r.get("ovrs_nmix_lwpr")), "Close": c})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame()


def fetch_global(name: str, start, end=None) -> pd.DataFrame:
    """start~end 를 100일 단위로 끊어 받는다(한 번에 주는 개수 제한 대비)."""
    from src import kis

    div, code, _ = GLOBAL[name]
    end = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
    parts, cur = [], pd.Timestamp(start)
    while cur <= end:
        b = min(cur + pd.Timedelta(days=99), end)
        body, _ = kis.call("FHKST03030100", "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice",
                           {"FID_COND_MRKT_DIV_CODE": div, "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": f"{cur:%Y%m%d}",
                            "FID_INPUT_DATE_2": f"{b:%Y%m%d}", "FID_PERIOD_DIV_CODE": "D"})
        parts.append(parse_global(list(body.get("output2") or [])))
        cur = b + pd.Timedelta(days=1)
    parts = [p for p in parts if len(p)]
    if not parts:
        raise ValueError(f"해외 지수 빈 응답: {name}")
    df = pd.concat(parts)
    return df[~df.index.duplicated(keep="last")].sort_index()


def parse_investor(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date", ""))
        if len(d) != 8:
            continue
        out.append({"Date": pd.Timestamp(d), "index": _num(r.get("bstp_nmix_prpr")),
                    "개인": _num(r.get("prsn_ntby_tr_pbmn")) * UNIT, "외국인": _num(r.get("frgn_ntby_tr_pbmn")) * UNIT,
                    "기관": _num(r.get("orgn_ntby_tr_pbmn")) * UNIT})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame()


def fetch_investor(mkt: str, end=None) -> pd.DataFrame:
    """end 까지 약 300거래일."""
    from src import kis
    from src.data.flows import last_complete_day

    iscd, cls = MARKETS[mkt]
    end = min(pd.Timestamp(end), last_complete_day()) if end is not None else last_complete_day()
    body, _ = kis.call("FHPTJ04040000", "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
                       {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd, "FID_INPUT_DATE_1": f"{end:%Y%m%d}",
                        "FID_INPUT_ISCD_1": cls, "FID_INPUT_DATE_2": f"{end:%Y%m%d}", "FID_INPUT_ISCD_2": iscd})
    df = parse_investor(list(body.get("output") or []))
    if df.empty:
        raise ValueError(f"시장별 투자자 빈 응답: {mkt}")
    return df


def calendar_path() -> Path:
    return config.DATA / "raw" / "kis_calendar.csv"


def parse_calendar(rows: list[dict]) -> pd.DataFrame:
    out = [{"Date": pd.Timestamp(str(r["bass_dt"])), "open": str(r.get("opnd_yn", "")).upper() == "Y"}
           for r in rows if len(str(r.get("bass_dt", ""))) == 8]
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame()


def fetch_calendar(base) -> pd.DataFrame:
    from src import kis

    body, _ = kis.call("CTCA0903R", "/uapi/domestic-stock/v1/quotations/chk-holiday",
                       {"BASS_DT": f"{pd.Timestamp(base):%Y%m%d}", "CTX_AREA_NK": "", "CTX_AREA_FK": ""})
    df = parse_calendar(list(body.get("output") or []))
    if df.empty:
        raise ValueError("휴장일 빈 응답")
    return df


def load_calendar() -> pd.Series:
    """날짜 → 개장 여부(bool). 없으면 빈 Series."""
    p = calendar_path()
    return pd.read_csv(p, index_col="Date", parse_dates=True)["open"].astype(bool) if p.exists() else pd.Series(dtype=bool)


def trading_lag(last, today, calendar: pd.Series, holidays=frozenset()) -> int:
    """last 다음 날부터 today 전날까지의 개장일 수 (KRX 는 D일 데이터를 D+1 아침에 준다 → 0~1 이 정상).

    KIS 달력에 있는 날은 달력을, 없는 날은 평일이면서 알려진 휴장일이 아닌지로 판단한다.
    """
    last, today = pd.Timestamp(last).normalize(), pd.Timestamp(today).normalize()
    n = 0
    for d in pd.date_range(last + pd.Timedelta(days=1), today - pd.Timedelta(days=1)):
        n += bool(calendar[d]) if d in calendar.index else (d.weekday() < 5 and d not in holidays)
    return n


def update(log, global_days: int = 400) -> int:
    fails = 0
    try:
        cal = _merge_save(calendar_path(), fetch_calendar(pd.Timestamp.today().normalize() - pd.Timedelta(days=10)))
        log.info("휴장일 달력 %d일 (~%s, 휴장 %d일)", len(cal), f"{cal.index.max():%Y-%m-%d}", int((~cal["open"]).sum()))
    except Exception as e:  # noqa: BLE001
        fails += 1
        log.warning("휴장일 달력: %s", e)
    for name in GLOBAL:
        p = global_path(name)
        start = (pd.read_csv(p, index_col="Date", parse_dates=True).index.max() - pd.Timedelta(days=7)) if p.exists() \
            else pd.Timestamp.today().normalize() - pd.Timedelta(days=global_days)
        try:
            df = _merge_save(p, fetch_global(name, start))
            log.info("해외 %s %d행 (~%s)", name, len(df), f"{df.index.max():%Y-%m-%d}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            log.warning("해외 %s: %s", name, e)
    for mkt in MARKETS:
        try:
            df = _merge_save(investor_path(mkt), fetch_investor(mkt))
            log.info("시장별 투자자 %s %d행 (~%s)", mkt, len(df), f"{df.index.max():%Y-%m-%d}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            log.warning("시장별 투자자 %s: %s", mkt, e)
    return 0 if not fails else 2


def load_global(name: str) -> pd.DataFrame:
    p = global_path(name)
    return pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame()


def load_investor(mkt: str) -> pd.DataFrame:
    p = investor_path(mkt)
    return pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame()
