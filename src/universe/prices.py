"""종목별 일봉 수집: yfinance -> KRX 2단계 (생존편향 방지 2단계).

yfinance 는 상장 중인 종목만 준다. 합병·상폐 종목은 빈 응답인데,
바로 그 종목들이 생존편향을 만든다. 그래서 KRX(pykrx adjusted=True)로 메운다.
저장: prices/<code>.csv  (Date, Open, High, Low, Close, Volume, source)
벤치마크: bench/<name>.csv (KODEX200=069500, KOSPI)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config, krx

COLS = ["Open", "High", "Low", "Close", "Volume"]
KR_MAP = {"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"}

# yfinance 시작일이 요청보다 이만큼 늦으면 KRX 로 앞부분이 있는지 확인한다.
LATE_START_DAYS = 30
# 증분 갱신 시 겹치는 구간 종가가 이 비율 이상 다르면 수정주가가 바뀐 것(분할·배당 등) → 전체 재수집.
ADJ_TOL = 0.005

BENCH = {
    # name: (yfinance 티커, KRX 종류, KRX 코드)
    "069500": ("069500.KS", "etf", "069500"),
    "KOSPI": ("^KS11", "index", "1001"),
}


def price_path(code: str) -> Path:
    return config.PRICES_DIR / f"{code}.csv"


def bench_path(name: str) -> Path:
    return config.BENCH_DIR / f"{name}.csv"


def clean(df: pd.DataFrame | None) -> pd.DataFrame:
    """한글 컬럼 매핑 + 0 종가(거래정지·상폐 직전) 제거 + 날짜 중복 제거 + 정렬."""
    if krx.is_empty(df):
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):  # yfinance 단일 티커도 (필드, 티커) 멀티인덱스
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=KR_MAP)
    if "Close" not in df.columns:
        return pd.DataFrame(columns=COLS)
    df = df[[c for c in COLS if c in df.columns]]
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "Date"
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df[df["Close"].fillna(0) > 0]
    return df[~df.index.duplicated(keep="last")].sort_index()


def _fmt(d) -> str:
    return pd.Timestamp(d).strftime("%Y%m%d")


def _today() -> pd.Timestamp:
    return pd.Timestamp.today().normalize()


def fetch_yf(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    # yfinance end 는 배타적이라 하루 더한다
    end_ex = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") if end else None
    df = yf.download(
        ticker, start=start, end=end_ex, auto_adjust=True, progress=False, threads=False
    )
    out = clean(df)
    if out.empty:
        raise ValueError(f"yfinance 빈 응답: {ticker}")
    return out


def fetch_krx(code: str, start: str, end: str | None = None, kind: str = "stock") -> pd.DataFrame:
    s = krx.stock()
    a, b = _fmt(start), _fmt(end or _today())
    krx.throttle()
    if kind == "stock":
        df = s.get_market_ohlcv_by_date(a, b, code, adjusted=True)
    elif kind == "etf":
        df = s.get_etf_ohlcv_by_date(a, b, code)
    elif kind == "index":
        df = s.get_index_ohlcv_by_date(a, b, code)
    else:
        raise ValueError(kind)
    out = clean(df)
    if out.empty:
        raise ValueError(f"KRX 빈 응답: {code}")
    return out


def fetch_any(code: str, start: str, end: str | None = None, krx_only: bool = False):
    """(df, source). 둘 다 실패하면 ValueError.

    yfinance 가 요청 시작일보다 한참 늦게 시작하면(yf 쪽 이력 누락 또는 신규상장)
    KRX 로 한 번 더 받아 더 긴 쪽을 쓴다.
    """
    errs = []
    if not krx_only:
        try:
            df = fetch_yf(f"{code}.KS", start, end)
            if (df.index.min() - pd.Timestamp(start)).days <= LATE_START_DAYS:
                return df, "yf"
            try:
                k = fetch_krx(code, start, end)
                if k.index.min() < df.index.min() - pd.Timedelta(days=LATE_START_DAYS):
                    return k, "krx"
            except Exception:  # noqa: BLE001
                pass
            return df, "yf"
        except Exception as e:  # noqa: BLE001
            errs.append(f"yf: {e}")
    try:
        return fetch_krx(code, start, end), "krx"
    except Exception as e:  # noqa: BLE001
        errs.append(f"krx: {e}")
    raise ValueError(f"{code} 실패 — " + " | ".join(errs))


def _load_path(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame(columns=COLS + ["source"])
    return pd.read_csv(p, index_col="Date", parse_dates=True)


def load(code: str) -> pd.DataFrame:
    return _load_path(price_path(code))


def load_bench(name: str) -> pd.DataFrame:
    return _load_path(bench_path(name))


def _save_path(p: Path, new: pd.DataFrame, source: str, overwrite: bool) -> pd.DataFrame:
    new = new.copy()
    new["source"] = source
    old = pd.DataFrame() if overwrite else _load_path(p)
    df = pd.concat([old, new]) if len(old) else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="Date")
    return df


def save(code: str, new: pd.DataFrame, source: str, overwrite: bool = False) -> pd.DataFrame:
    return _save_path(price_path(code), new, source, overwrite)


def auto_period_start(last: pd.Timestamp | None, today: pd.Timestamp | None = None,
                      default_years: int = 10) -> str:
    """증분 갱신 시작일. 캐시 마지막일 -5일부터 다시 받아 겹치는 구간으로 수정주가 변동을 감지."""
    today = today or _today()
    if last is None or pd.isna(last):
        return (today - pd.DateOffset(years=default_years)).strftime("%Y-%m-%d")
    return (pd.Timestamp(last) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")


def adjustment_changed(old: pd.DataFrame, new: pd.DataFrame, tol: float = ADJ_TOL) -> bool:
    ov = old.index.intersection(new.index)
    if len(ov) == 0:
        return False
    rel = (new.loc[ov, "Close"].astype(float) / old.loc[ov, "Close"].astype(float) - 1).abs()
    return bool(rel.max() > tol)


def backfill(code: str, start: str, krx_only: bool = False) -> tuple[pd.DataFrame, str]:
    df, src = fetch_any(code, start, krx_only=krx_only)
    return save(code, df, src, overwrite=True), src


def update(code: str, default_start: str) -> tuple[pd.DataFrame, str]:
    """증분 갱신. 겹치는 구간 종가가 달라졌으면 처음부터 다시 받아 덮어쓴다."""
    old = load(code)
    if old.empty:
        return backfill(code, default_start)
    last_src = str(old["source"].iloc[-1]) if "source" in old.columns else "yf"
    start = auto_period_start(old.index.max())
    new, src = fetch_any(code, start, krx_only=(last_src == "krx"))
    if adjustment_changed(old, new) or src != last_src:
        full_start = min(old.index.min(), pd.Timestamp(default_start)).strftime("%Y-%m-%d")
        return backfill(code, full_start, krx_only=(src == "krx"))
    return save(code, new, src), src


def fetch_bench(name: str, start: str, end: str | None = None) -> tuple[pd.DataFrame, str]:
    yf_t, kind, kcode = BENCH[name]
    errs = []
    try:
        df = fetch_yf(yf_t, start, end)
        if (df.index.min() - pd.Timestamp(start)).days <= LATE_START_DAYS:
            return df, "yf"
        errs.append(f"yf: 시작일 {df.index.min():%Y-%m-%d} 로 늦음")
    except Exception as e:  # noqa: BLE001
        errs.append(f"yf: {e}")
    try:
        return fetch_krx(kcode, start, end, kind=kind), "krx"
    except Exception as e:  # noqa: BLE001
        errs.append(f"krx: {e}")
    raise ValueError(f"벤치 {name} 실패 — " + " | ".join(errs))


def save_bench(name: str, df: pd.DataFrame, source: str) -> pd.DataFrame:
    return _save_path(bench_path(name), df, source, overwrite=True)


def delisted_guess(codes: list[str], today: pd.Timestamp | None = None, gap_days: int = 20):
    """마지막 거래일이 오늘보다 gap_days 넘게 과거 = 상장폐지/합병 추정."""
    today = today or _today()
    out = []
    for c in codes:
        df = load(c)
        if len(df) and (today - df.index.max()).days > gap_days:
            out.append((c, df.index.max().date()))
    return out


def panel(codes: list[str], field: str = "Close") -> pd.DataFrame:
    """필드 패널(날짜×종목). 전 종목 NaN 인 행(휴장일)은 수익률 계산 전에 반드시 뺀다."""
    cols = {}
    for c in codes:
        if price_path(c).exists():
            df = load(c)
            if field in df.columns:
                cols[c] = df[field]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().dropna(how="all")


def close_panel(codes: list[str]) -> pd.DataFrame:
    return panel(codes, "Close")
