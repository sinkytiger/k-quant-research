"""종목별 일봉 저장소 + yfinance 보조 경로.

국내 주 경로는 KRX Open API 일별 스냅샷(src/universe/krx_daily.py)이 이 파일 형식으로 써 준다.
yfinance 경로(fetch_yf / backfill / update)는 미국 종목용이다. yfinance 는 상장 중인 종목만 주므로
국내 백테스트에 쓰면 합병·상폐 종목이 빠져 생존편향이 생긴다.
저장: prices/<code>.csv  (Date, Open, High, Low, Close, Volume, source)
벤치마크: bench/<name>.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

COLS = ["Open", "High", "Low", "Close", "Volume"]
KR_MAP = {"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"}

# 증분 갱신 시 겹치는 구간 종가가 이 비율 이상 다르면 수정주가가 바뀐 것(분할 등) → 전체 재수집.
ADJ_TOL = 0.005


def price_path(code: str) -> Path:
    return config.PRICES_DIR / f"{code}.csv"


def bench_path(name: str) -> Path:
    return config.BENCH_DIR / f"{name}.csv"


def clean(df: pd.DataFrame | None) -> pd.DataFrame:
    """컬럼 정리 + 0 종가 제거 + 날짜 중복 제거 + 정렬."""
    if df is None or len(df) == 0:
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


def backfill_yf(ticker: str, start: str, code: str | None = None) -> pd.DataFrame:
    return save(code or ticker, fetch_yf(ticker, start), "yf", overwrite=True)


def update_yf(ticker: str, default_start: str, code: str | None = None) -> pd.DataFrame:
    """yfinance 증분. 겹치는 구간 종가가 달라졌으면 처음부터 다시 받아 덮어쓴다."""
    code = code or ticker
    old = load(code)
    if old.empty:
        return backfill_yf(ticker, default_start, code)
    new = fetch_yf(ticker, auto_period_start(old.index.max()))
    if adjustment_changed(old, new):
        full_start = min(old.index.min(), pd.Timestamp(default_start)).strftime("%Y-%m-%d")
        return backfill_yf(ticker, full_start, code)
    return save(code, new, "yf")


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
