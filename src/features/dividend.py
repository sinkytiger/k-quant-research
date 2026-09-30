"""배당 피처 (사전 등록 div_sig_v1).

- 배당금을 아는 날 = 지급일(없으면 기준일 + 120일). 그날 이후 거래일부터 피처에 들어간다.
- 분할 환산: 주당배당금 × (권리부 마지막 날 수정가 ÷ 원시가) → 현재 주식 기준.
- dy_ttm = 지급일이 (t−365일, t] 인 환산 배당 합 ÷ t 수정 종가 (무배당 0)
- div_growth = TTM_t ÷ TTM_{t−365일} − 1 (둘 다 > 0 일 때만)
"""
from __future__ import annotations

import pandas as pd

from src.data import dividends
from src.universe import total_return as tr

KNOWN_LAG_IF_NO_PAY = pd.Timedelta(days=120)


def known_dividends(code: str, adj_close: pd.Series, trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    """columns: known_day(거래일), dps_adj. 분할 환산된 주당배당금과 그것을 알게 되는 첫 거래일."""
    td = pd.DatetimeIndex(trading_days)
    divs = dividends.load(code)
    ys = tr.dividend_yields(code, td)  # cum_date, raw_close (원시 종가)
    if divs.empty or ys.empty:
        return pd.DataFrame(columns=["known_day", "dps_adj"])
    rows = []
    for d in divs.itertuples():
        cum, _ = tr.ex_date(d.record_date, td)
        if cum is None:
            continue
        m = ys[ys["cum_date"] == cum]
        if m.empty or cum not in adj_close.index or pd.isna(adj_close.get(cum)):
            continue
        factor = float(adj_close[cum]) / float(m["raw_close"].iloc[0])
        pay = pd.to_datetime(str(d.pay_date), errors="coerce")
        known = pay if pd.notna(pay) else d.record_date + KNOWN_LAG_IF_NO_PAY
        k = td.searchsorted(known, side="left")
        if k >= len(td):
            continue
        rows.append({"known_day": td[k], "dps_adj": d.dps * factor})
    return pd.DataFrame(rows, columns=["known_day", "dps_adj"])


def ttm(known: pd.DataFrame, trading_days: pd.DatetimeIndex) -> pd.Series:
    """거래일별 (t−365일, t] 에 알게 된 환산 배당 합."""
    td = pd.DatetimeIndex(trading_days)
    s = known.groupby("known_day")["dps_adj"].sum() if len(known) else pd.Series(dtype=float)
    daily = pd.Series(0.0, index=td)
    daily.loc[s.index[s.index.isin(td)]] = s[s.index.isin(td)].values
    return daily.rolling("365D").sum()


def features(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """close: 수정 가격 패널(가격수익률 기준, TR 아님)."""
    td = close.index
    dy, gr = {}, {}
    for c in close.columns:
        t = ttm(known_dividends(c, close[c], td), td)
        dy[c] = t / close[c]
        prev = t.reindex(td - pd.Timedelta(days=365), method="ffill").values
        prev = pd.Series(prev, index=td)
        gr[c] = (t / prev - 1).where((t > 0) & (prev > 0))
    return {"dy_ttm": pd.DataFrame(dy), "div_growth": pd.DataFrame(gr)}
