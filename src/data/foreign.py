"""외국인 지분율 (KIS 공식 API).

주식현재가 일자별 FHKST01010400 (`/quotations/inquire-daily-price`) 의 hts_frgn_ehrt = 외국인 한도 소진율(%) = 보유 ÷ 외국인 한도 주식.
한도가 없는 종목은 한도 = 상장주식이라 소진율 = 지분율. 한도 종목(항공·통신·전력 등)은 다르다
— 대한항공 소진율 53.27% = 지분율 26.63% (한도 약 50%), KT 소진율 100% = 지분율 49%.
→ 주식현재가 시세 FHKST01010100 의 외국인 보유 주수 ÷ 상장주식 ÷ 소진율 로 종목별 한도(%)를 구해 limits.csv 에 두고,
  지분율 = 소진율 × 한도 ÷ 100.
일자별 API 는 기간 지정이 없고 한 번에 30행만 준다 → 일(30거래일)·주(약 30주)·월(29개월) 세 번 받아 합친다.
주·월 행의 값은 그 기간 마지막 거래일 값이다(이번 달 행 = 오늘 값). 같은 날짜는 일별 값이 우선.
매일 일별 30행을 받아 붙이므로 앞으로는 일별 이력이 쌓인다.
저장: raw/foreign/<코드>.csv (Date, ehrt, freq), raw/foreign/limits.csv (code, limit, asof)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

TR, PATH = "FHKST01010400", "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
PRICE_TR, PRICE_PATH = "FHKST01010100", "/uapi/domestic-stock/v1/quotations/inquire-price"
RANK = {"D": 0, "W": 1, "M": 2}  # 같은 날짜면 일별 우선
COLS = ["ehrt", "freq"]


def path(code: str) -> Path:
    return config.DATA / "raw" / "foreign" / f"{code}.csv"


def limits_path() -> Path:
    return config.DATA / "raw" / "foreign" / "limits.csv"


def _num(x):
    return pd.to_numeric(str(x if x is not None else "").replace(",", ""), errors="coerce")


def parse(rows: list[dict], freq: str) -> pd.DataFrame:
    out = []
    for r in rows:
        d = str(r.get("stck_bsop_date", ""))
        ehrt, close = _num(r.get("hts_frgn_ehrt")), _num(r.get("stck_clpr"))
        if len(d) != 8 or not close > 0 or ehrt != ehrt:
            continue
        out.append({"Date": pd.Timestamp(d), "ehrt": float(ehrt), "freq": freq})
    return pd.DataFrame(out).set_index("Date").sort_index() if out else pd.DataFrame(columns=COLS)


def merge(*frames: pd.DataFrame) -> pd.DataFrame:
    frames = [f[COLS] for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames)
    df = df.assign(_r=df["freq"].map(RANK).fillna(9)).reset_index().sort_values(["Date", "_r"], kind="stable")
    return df.drop_duplicates("Date", keep="first").set_index("Date").drop(columns="_r").sort_index()


def fetch(code: str, freqs=("D", "W", "M")) -> pd.DataFrame:
    from src import kis

    parts = []
    for f in freqs:
        body, _ = kis.call(TR, PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                                      "FID_PERIOD_DIV_CODE": f, "FID_ORG_ADJ_PRC": "0"})
        parts.append(parse(list(body.get("output") or []), f))
    return merge(*parts)


def limit_from(hold_qty, listed, ehrt) -> float | None:
    """외국인 한도(상장주식 대비 %). 소진율이 0 이거나 값이 없으면 None. 99.5 넘으면 한도 없음 = 100."""
    if not listed or not ehrt or not ehrt > 0 or hold_qty != hold_qty:
        return None
    lim = hold_qty / listed * 100 / ehrt * 100
    return 100.0 if lim > 99.5 else round(float(lim), 2)


def fetch_limit(code: str) -> float | None:
    from src import kis

    body, _ = kis.call(PRICE_TR, PRICE_PATH, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    o = body.get("output") or {}
    return limit_from(_num(o.get("frgn_hldn_qty")), _num(o.get("lstn_stcn")), _num(o.get("hts_frgn_ehrt")))


def load_limits() -> dict[str, float]:
    p = limits_path()
    if not p.exists():
        return {}
    d = pd.read_csv(p, dtype={"code": str})
    return dict(zip(d["code"], d["limit"]))


def save_limits(new: dict[str, float | None]) -> None:
    cur = load_limits()
    cur.update({k: v for k, v in new.items() if v is not None})
    p = limits_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"code": list(cur), "limit": list(cur.values())}).assign(asof=f"{pd.Timestamp.today():%Y-%m-%d}") \
        .sort_values("code").to_csv(p, index=False)


def _raw(code: str) -> pd.DataFrame:
    p = path(code)
    return pd.read_csv(p, index_col="Date", parse_dates=True) if p.exists() else pd.DataFrame(columns=COLS)


def load(code: str, limits: dict[str, float] | None = None) -> pd.DataFrame:
    """ehrt(소진율) + ratio(지분율 = 소진율 × 한도 ÷ 100). 한도를 모르면 지분율 = 소진율. df.attrs["limit"] = 한도."""
    df = _raw(code)
    lim = float((limits if limits is not None else load_limits()).get(code, 100.0))
    df["ratio"] = (df["ehrt"] * lim / 100).round(3) if len(df) else pd.Series(dtype=float)
    df.attrs["limit"] = lim
    return df


def save(code: str, new: pd.DataFrame) -> pd.DataFrame:
    df = merge(new, _raw(code))  # 새 값이 앞 → 같은 날짜면 (주기 우선순위가 같을 때) 새 값
    p = path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="Date")
    return df


def change(df: pd.DataFrame, days: int, col: str = "ratio") -> float | None:
    """마지막 값 − (마지막 날짜 − days 일) 이전 마지막 값 (%p)."""
    if df.empty:
        return None
    s = df[col]
    past = s[s.index <= s.index.max() - pd.Timedelta(days=days)]
    return None if past.empty else round(float(s.iloc[-1] - past.iloc[-1]), 3)
