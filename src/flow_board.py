"""대시보드 수급 탭: 종목별 투자자 순매수 기간 합계·연속 순매수 일수, 시장별 투자자 추이.

- 입력은 KIS 투자자별 매매(원). 기간 합계는 마지막 n 거래일 합(그 종목 데이터가 있는 날 기준).
- 연속 일수: 마지막 거래일부터 거꾸로 같은 부호(순매수 +, 순매도 −)가 이어진 날 수. 0 이나 결측에서 끊는다.
  순매수 연속이면 양수, 순매도 연속이면 음수로 돌려준다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

INVESTORS = {"외국인합계": "frgn", "기관합계": "inst", "개인": "indiv", "기타법인": "corp"}
WINDOWS = (1, 5, 20, 60)


def window_sums(panel: pd.DataFrame, windows=WINDOWS) -> pd.DataFrame:
    """날짜×종목 패널 → 종목×기간 합계 (열 이름 = 기간 일수)."""
    p = panel.sort_index()
    return pd.DataFrame({n: p.iloc[-n:].sum(min_count=1) for n in windows})


def streak(values) -> int:
    """마지막 값부터 같은 부호가 이어진 길이. 순매수면 +, 순매도면 −."""
    v = [x for x in values]
    if not v or v[-1] is None or (isinstance(v[-1], float) and np.isnan(v[-1])) or v[-1] == 0:
        return 0
    sign = 1 if v[-1] > 0 else -1
    n = 0
    for x in reversed(v):
        if x is None or (isinstance(x, float) and np.isnan(x)) or x == 0 or (x > 0) != (sign > 0):
            break
        n += 1
    return sign * n


def streaks(panel: pd.DataFrame, lookback: int = 120) -> pd.Series:
    p = panel.sort_index().iloc[-lookback:]
    return pd.Series({c: streak(list(p[c].astype(float))) for c in p.columns}, dtype=int)


def returns(close: pd.DataFrame, asof, windows=WINDOWS) -> pd.DataFrame:
    """asof 까지의 기간 수익률 (수정주가). 종목×기간."""
    c = close.loc[:asof].ffill()
    return pd.DataFrame({n: c.iloc[-1] / c.iloc[-n - 1] - 1 if len(c) > n else np.nan for n in windows})


def market_series(inv: pd.DataFrame, days: int = 260) -> dict:
    """시장별 투자자 일별 순매수(원) → 화면용 {dates, 개인, 외국인, 기관}."""
    d = inv.sort_index().iloc[-days:]
    out = {"dates": [f"{x:%Y-%m-%d}" for x in d.index]}
    for k in ("개인", "외국인", "기관"):
        out[k] = [None if pd.isna(v) else float(v) for v in d[k]]
    return out
