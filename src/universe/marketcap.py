"""시가총액 스냅샷: get_market_cap(date) 한 번에 전 종목.

함정: 휴장일(토요일 등)을 넣으면 예외 대신 **시가총액이 전부 0인 표**를 준다.
그래서 cap>0 이 하나도 없으면 ValueError 로 보고, 영업일 기준 최대 5일 거슬러 재시도한다.
0 뿐인 파일은 없는 것으로 보고 --update 에서 다시 받는다.
저장: market_cap/<YYYYMMDD>.csv (파일명 = 실제 거래일)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config, krx

COL_MAP = {"종가": "close", "시가총액": "market_cap", "거래량": "volume",
           "거래대금": "value", "상장주식수": "shares"}
MAX_BACK = 5


def cap_path(day) -> Path:
    return config.MARKETCAP_DIR / f"{pd.Timestamp(day):%Y%m%d}.csv"


def validate(df: pd.DataFrame | None) -> pd.DataFrame:
    """pykrx 원표 → 정리된 표. 쓸 수 없으면 ValueError."""
    if krx.is_empty(df) or "시가총액" not in df.columns:
        raise ValueError("시가총액 표가 비어 있음")
    caps = pd.to_numeric(df["시가총액"], errors="coerce").fillna(0)
    if not (caps > 0).any():
        raise ValueError("시가총액이 전부 0 (휴장일 추정)")
    out = df.rename(columns=COL_MAP)
    out = out[[c for c in COL_MAP.values() if c in out.columns]].copy()
    out.index = [str(c).zfill(6) for c in out.index]
    out.index.name = "code"
    return out


def fetch_cap(day, market: str = "KOSPI", max_back: int = MAX_BACK) -> tuple[pd.Timestamp, pd.DataFrame]:
    """(실제 거래일, 표). 주말은 건너뛰고 평일만 최대 max_back 번 거슬러 올라간다."""
    s = krx.stock()
    d = pd.Timestamp(day).normalize()
    tries = 0
    errs = []
    while tries <= max_back:
        if d.weekday() < 5:
            try:
                krx.throttle()
                raw = s.get_market_cap(d.strftime("%Y%m%d"), market=market)
                return d, validate(raw)
            except ValueError as e:
                errs.append(f"{d:%Y%m%d}: {e}")
            tries += 1
        d -= pd.Timedelta(days=1)
    raise ValueError(f"{pd.Timestamp(day):%Y-%m-%d} 부터 {max_back}영업일 거슬러도 실패 — " + "; ".join(errs))


def save(day, df: pd.DataFrame) -> Path:
    p = cap_path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="code")
    return p


def load(day) -> pd.DataFrame:
    return pd.read_csv(cap_path(day), dtype={"code": str}, index_col="code")


def is_valid_file(p: Path) -> bool:
    """0 뿐인 파일(휴장일 표가 저장된 경우)은 없는 것으로 본다."""
    try:
        df = pd.read_csv(p, usecols=["market_cap"])
    except Exception:  # noqa: BLE001
        return False
    return bool((pd.to_numeric(df["market_cap"], errors="coerce").fillna(0) > 0).any())


def saved_days(valid_only: bool = True) -> list[pd.Timestamp]:
    if not config.MARKETCAP_DIR.exists():
        return []
    out = []
    for p in sorted(config.MARKETCAP_DIR.glob("*.csv")):
        if not valid_only or is_valid_file(p):
            out.append(pd.Timestamp(p.stem))
    return out


def invalid_files() -> list[Path]:
    if not config.MARKETCAP_DIR.exists():
        return []
    return [p for p in sorted(config.MARKETCAP_DIR.glob("*.csv")) if not is_valid_file(p)]


def cap_asof(when, codes=None) -> pd.Series:
    """when 이전 가장 최근 스냅샷의 시가총액. 미래 스냅샷은 보지 않는다."""
    days = [d for d in saved_days(valid_only=False) if d <= pd.Timestamp(when)]
    if not days:
        return pd.Series(dtype=float)
    s = load(max(days))["market_cap"]
    return s if codes is None else s.reindex(list(codes))
