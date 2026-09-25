"""투자자별 순매수 수급 (가격과 직교한 첫 번째 축).

- 도구: get_market_trading_value_by_date(from, to, code, on="순매수"), 원 단위
- 저장: flows/<code>.csv (Date, 기관합계, 기타법인, 개인, 외국인합계)
- 피처: 순매수 ÷ 최근 20일 거래대금 중앙값(shift 1), 공표 시차 lag=1.
  원화 그대로 쓰면 삼성전자가 늘 1등이다.
- 가중치: 수집만 한다. picks 점수 가중치는 IC 를 재기 전까지 0.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config, krx

FLOW_COLS = ["기관합계", "기타법인", "개인", "외국인합계"]
LAG = 1
NORM_WINDOW = 20


def flow_path(code: str) -> Path:
    return config.FLOWS_DIR / f"{code}.csv"


def _year_chunks(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    out = []
    a = start
    while a <= end:
        b = min(a + pd.DateOffset(years=1) - pd.Timedelta(days=1), end)
        out.append((a, b))
        a = b + pd.Timedelta(days=1)
    return out


def clean(df: pd.DataFrame | None) -> pd.DataFrame:
    if krx.is_empty(df):
        return pd.DataFrame(columns=FLOW_COLS)
    cols = [c for c in FLOW_COLS if c in df.columns]
    out = df[cols].apply(pd.to_numeric, errors="coerce")
    out.index = pd.to_datetime(out.index).normalize()
    out.index.name = "Date"
    return out[~out.index.duplicated(keep="last")].sort_index()


def fetch(code: str, start, end=None) -> pd.DataFrame:
    """1년 단위로 끊어 받는다. 전 구간이 비면 ValueError."""
    s = krx.stock()
    end_ts = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
    parts = []
    for a, b in _year_chunks(pd.Timestamp(start), end_ts):
        krx.throttle()
        raw = s.get_market_trading_value_by_date(
            a.strftime("%Y%m%d"), b.strftime("%Y%m%d"), code, on="순매수"
        )
        c = clean(raw)
        if len(c):
            parts.append(c)
    if len(parts) == 0:
        raise ValueError(f"수급 빈 응답: {code}")
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="last")].sort_index()


def load(code: str) -> pd.DataFrame:
    p = flow_path(code)
    if not p.exists():
        return pd.DataFrame(columns=FLOW_COLS)
    return pd.read_csv(p, index_col="Date", parse_dates=True, encoding="utf-8-sig")


def save(code: str, new: pd.DataFrame, overwrite: bool = False) -> pd.DataFrame:
    old = pd.DataFrame() if overwrite else load(code)
    df = pd.concat([old, new]) if len(old) else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    flow_path(code).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(flow_path(code), index_label="Date", encoding="utf-8-sig")
    return df


def update_start(code: str, default_start) -> pd.Timestamp:
    """증분: 캐시 마지막일 -5일부터 다시 받는다(KRX 수정 반영)."""
    old = load(code)
    if old.empty:
        return pd.Timestamp(default_start)
    return old.index.max() - pd.Timedelta(days=5)


def flow_panel(codes: list[str], investor: str) -> pd.DataFrame:
    cols = {}
    for c in codes:
        df = load(c)
        if investor in df.columns and len(df):
            cols[c] = df[investor]
    return pd.DataFrame(cols).sort_index()


def normalized_flow(
    flow: pd.DataFrame,
    value: pd.DataFrame,
    lag: int = LAG,
    window: int = NORM_WINDOW,
) -> pd.DataFrame:
    """t 시점에 쓸 수 있는 수급 피처 = flow_{t-lag} / median(value_{t-window..t-1}).

    flow, value 는 같은 거래일 인덱스(휴장일 행 제거된)여야 한다.
    분모도 shift(1): 오늘 거래대금은 장 마감 전엔 모른다.
    """
    idx = value.index.union(flow.index)
    value = value.reindex(idx)
    flow = flow.reindex(index=idx, columns=value.columns)
    denom = value.rolling(window, min_periods=max(5, window // 2)).median().shift(1)
    denom = denom.where(denom > 0)
    return flow.shift(lag) / denom
