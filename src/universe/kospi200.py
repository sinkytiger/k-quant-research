"""KOSPI200 시점별 구성종목 (생존편향 방지 1단계).

월 1회(매월 1일) 스냅샷을 membership/<YYYYMMDD>.csv 로 저장하고,
각 날짜에는 그 날짜 이전 스냅샷 중 가장 최근 것만 쓴다(members_asof).
티커는 지어내지 않는다. 스냅샷이 없으면 빈 집합을 돌려주고 호출자가
survivorship_safe=False 를 찍는다.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src import config, krx


def month_starts(start: str, end: str | None = None) -> list[pd.Timestamp]:
    end_ts = pd.Timestamp(end) if end else pd.Timestamp(date.today())
    return list(pd.date_range(pd.Timestamp(start), end_ts, freq="MS"))


def fetch_members(asof: pd.Timestamp) -> list[str]:
    """alternative=True: 휴장일이면 직전 영업일 구성으로 대체."""
    krx.throttle()
    codes = krx.stock().get_index_portfolio_deposit_file(
        config.KOSPI200_INDEX, pd.Timestamp(asof).strftime("%Y%m%d"), alternative=True
    )
    if krx.is_empty(codes):
        return []
    return sorted({str(c).zfill(6) for c in list(codes)})


def snapshot_path(asof: pd.Timestamp) -> Path:
    return config.MEMBERSHIP_DIR / f"{pd.Timestamp(asof):%Y%m%d}.csv"


def save_snapshot(asof: pd.Timestamp, codes: list[str]) -> Path:
    if len(codes) == 0:
        raise ValueError(f"빈 스냅샷은 저장하지 않는다: {asof:%Y-%m-%d}")
    p = snapshot_path(asof)
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"code": codes}).to_csv(p, index=False)
    return p


def load_membership() -> dict[pd.Timestamp, frozenset[str]]:
    out: dict[pd.Timestamp, frozenset[str]] = {}
    if not config.MEMBERSHIP_DIR.exists():
        return out
    for p in sorted(config.MEMBERSHIP_DIR.glob("*.csv")):
        df = pd.read_csv(p, dtype={"code": str})
        if len(df):
            out[pd.Timestamp(p.stem)] = frozenset(df["code"].str.zfill(6))
    return out


def members_asof(when, membership: dict | None = None) -> frozenset[str]:
    """when 시점에 실제로 지수에 있던 종목. when 이후 스냅샷은 절대 보지 않는다."""
    m = load_membership() if membership is None else membership
    ts = pd.Timestamp(when)
    past = [d for d in m if d <= ts]
    return m[max(past)] if past else frozenset()


def all_members(membership: dict | None = None) -> list[str]:
    """10년 동안 한 번이라도 편입됐던 모든 종목(상폐·합병 포함)."""
    m = load_membership() if membership is None else membership
    s: set[str] = set()
    for v in m.values():
        s |= v
    return sorted(s)


def current_members(membership: dict | None = None) -> frozenset[str]:
    m = load_membership() if membership is None else membership
    return m[max(m)] if m else frozenset()


def survivorship_safe(membership: dict | None = None) -> bool:
    m = load_membership() if membership is None else membership
    return len(m) > 0


# ---------------- 종목명 ----------------
def load_names() -> dict[str, str]:
    if not config.NAMES_CSV.exists():
        return {}
    df = pd.read_csv(config.NAMES_CSV, dtype={"code": str, "name": str}, encoding="utf-8-sig")
    return dict(zip(df["code"].str.zfill(6), df["name"]))


def save_names(new: dict[str, str]) -> dict[str, str]:
    """기존 캐시와 병합. 이름==코드(조회 실패값)는 기존 이름을 덮지 않는다."""
    merged = load_names()
    for code, name in new.items():
        code = str(code).zfill(6)
        if not name or str(name) == code or str(name).lower() in ("nan", "none"):
            merged.setdefault(code, code)
            continue
        merged[code] = str(name)
    config.NAMES_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sorted(merged.items()), columns=["code", "name"]).to_csv(
        config.NAMES_CSV, index=False, encoding="utf-8-sig"
    )
    return merged


def _bulk_names(today: pd.Timestamp | None = None) -> dict[str, str]:
    """get_market_ticker_name 이 깨졌을 때(2026-09-05~) 폴백: 등락률 표의 '종목명' 열."""
    s = krx.stock()
    end = today or pd.Timestamp(date.today())
    start = end - pd.Timedelta(days=10)
    out: dict[str, str] = {}
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            krx.throttle()
            df = s.get_market_price_change(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), market=mkt
            )
        except Exception:  # noqa: BLE001
            continue
        if krx.is_empty(df) or "종목명" not in df.columns:
            continue
        for code, name in df["종목명"].items():
            out[str(code).zfill(6)] = str(name)
    return out


def fetch_names(codes: list[str]) -> dict[str, str]:
    """상장 종목은 등락률 표로 한 번에, 나머지(상폐 등)만 종목별 조회. 실패하면 코드 그대로."""
    bulk = _bulk_names()
    s = krx.stock()
    out: dict[str, str] = {}
    for c in codes:
        if c in bulk:
            out[c] = bulk[c]
            continue
        try:
            krx.throttle()
            n = s.get_market_ticker_name(c)
            out[c] = n if isinstance(n, str) and n else c
        except Exception:  # noqa: BLE001
            out[c] = c
    return out
