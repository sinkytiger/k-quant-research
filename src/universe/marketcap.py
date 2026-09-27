"""시가총액 스냅샷 저장소. 파일은 krx_daily.build_marketcap 이 KRX Open API 일별매매로 만든다.

저장: market_cap/<YYYYMMDD>.csv (code, close, market_cap, volume, value, shares). 파일명 = 실제 거래일.
시가총액이 전부 0 인 파일은 무효로 본다(없는 것으로 취급하고 다시 만든다).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config


def cap_path(day) -> Path:
    return config.MARKETCAP_DIR / f"{pd.Timestamp(day):%Y%m%d}.csv"


def save(day, df: pd.DataFrame) -> Path:
    p = cap_path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index_label="code")
    return p


def load(day) -> pd.DataFrame:
    return pd.read_csv(cap_path(day), dtype={"code": str}, index_col="code")


def is_valid_file(p: Path) -> bool:
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


def cap_panel(codes=None, start=None, end=None) -> pd.DataFrame:
    """날짜 × 종목 시가총액 패널 (그날 종가 기준 = 그날 장 마감 후에 아는 값)."""
    want = None if codes is None else set(codes)
    cols = {}
    for d in saved_days():
        if (start is not None and d < pd.Timestamp(start)) or (end is not None and d > pd.Timestamp(end)):
            continue
        s = load(d)["market_cap"]
        cols[d] = s if want is None else s[s.index.isin(want)]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).T.sort_index()


def cap_asof(when, codes=None) -> pd.Series:
    """when 이전 가장 최근 스냅샷의 시가총액. 미래 스냅샷은 보지 않는다."""
    days = [d for d in saved_days() if d <= pd.Timestamp(when)]
    if not days:
        return pd.Series(dtype=float)
    s = load(max(days))["market_cap"]
    return s if codes is None else s.reindex(list(codes))
