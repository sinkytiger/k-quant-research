"""배당 포함 총수익(TR) 가격.

- 배당락일: 결제 T+2. "매수일 + 2거래일 ≤ 기준일"인 마지막 거래일이 권리부 마지막 날(cum),
  그다음 거래일이 배당락일(ex). 예) 기준일 2024-12-31(휴장) → cum 12-26, ex 12-27.
- 배당수익률 y = 주당 배당금 / cum 일 **원시** 종가 (시가총액 스냅샷의 close). 분할 조정과 무관.
- TR 가격: 배당락일의 총수익 = 가격수익률 + y. 수정주가에 (1 + r + y)/(1 + r) 를 누적 곱한다.
  (배당은 배당락일 종가에 재투자된 것으로 본다. 세금·지급 지연은 무시)
- 원시 종가를 찾지 못한 배당은 건너뛰고 수를 센다.
"""
from __future__ import annotations

import pandas as pd

from src.data import dividends
from src.universe import marketcap, prices


def ex_date(record_date, trading_days: pd.DatetimeIndex, settle: int = 2):
    """(cum_date, ex_date). 범위를 벗어나면 (None, None)."""
    td = pd.DatetimeIndex(trading_days)
    r = pd.Timestamp(record_date)
    last = td.searchsorted(r, side="right") - 1  # 기준일 이하 마지막 거래일
    # cum = td[k] 중 td[k + settle] <= r 인 가장 늦은 것 → k + settle = last
    k = last - settle
    if last < 0 or k < 0 or k + 1 >= len(td):
        return None, None
    return td[k], td[k + 1]


def dividend_yields(code: str, trading_days: pd.DatetimeIndex, raw_close: pd.Series | None = None) -> pd.DataFrame:
    """columns: ex_date, cum_date, dps, raw_close, yield. raw_close: 날짜→원시 종가(없으면 시총 스냅샷에서)."""
    divs = dividends.load(code)
    rows, missing = [], 0
    for d in divs.itertuples():
        cum, ex = ex_date(d.record_date, trading_days)
        if ex is None:
            continue
        px = raw_close.get(cum) if raw_close is not None else _raw_close(code, cum)
        if px is None or not px > 0:
            missing += 1
            continue
        rows.append({"ex_date": ex, "cum_date": cum, "dps": d.dps, "raw_close": float(px), "yield": d.dps / float(px)})
    out = pd.DataFrame(rows, columns=["ex_date", "cum_date", "dps", "raw_close", "yield"])
    out.attrs["missing"] = missing
    return out.groupby("ex_date", as_index=False).agg({"cum_date": "first", "dps": "sum", "raw_close": "first", "yield": "sum"})


def _raw_close(code: str, day) -> float | None:
    p = marketcap.cap_path(day)
    if not p.exists():
        return None
    df = marketcap.load(day)
    return float(df.at[code, "close"]) if code in df.index else None


def tr_close(close: pd.Series, yields: pd.DataFrame) -> pd.Series:
    """수정주가 → 총수익 가격 (첫날 같은 값에서 시작)."""
    close = close.dropna()
    r = close.pct_change(fill_method=None).fillna(0.0)
    y = pd.Series(0.0, index=close.index)
    if len(yields):
        yy = yields.set_index("ex_date")["yield"]
        yy = yy[yy.index.isin(close.index)]
        y.loc[yy.index] = yy.values
    mult = ((1 + r + y) / (1 + r)).cumprod()
    return close * mult


def tr_panel(codes: list[str]) -> pd.DataFrame:
    """종목별 TR 가격 패널 (배당 파일이 없는 종목은 가격수익률 그대로)."""
    close = prices.panel(codes, "Close")
    td = close.index
    cols = {}
    for c in close.columns:
        cols[c] = tr_close(close[c], dividend_yields(c, td))
    return pd.DataFrame(cols).reindex(td)
