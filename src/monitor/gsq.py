"""gs-quant(Goldman Sachs, Apache-2.0) `gs_quant.timeseries` 와 같은 정의의 pandas 구현.

gs-quant 를 런타임 의존성으로 두지 않는다. 설치하면 numpy 를 내려 다른 모듈을
흔들기 때문이다. 대신 별도 가상환경(C:\\KQuantData\\venv-gsq)에 gs-quant 를 깔고
tests/test_gsq_reference.py 로 값이 같은지 대조한다(그 환경에서만 돈다).
2026-10-07 gs-quant 2.1.18 과 대조 11건 일치(상대오차 1e-9, 베타 1e-7).
그때 exponential_std 가 adjust=True 로 달라 있던 것을 gs-quant 와 같은 adjust=False 로 고쳤다.

단위 규약 (gs-quant 와 같다)
- returns: 단순수익률(비율)
- volatility / exponential_volatility: **연율화 퍼센트** (20.0 = 20%). 비율이 필요하면 /100.
- max_drawdown: 0 이하 비율 (−0.25 = −25%)
- window=None 은 전 구간(expanding), 정수면 그 길이의 rolling. ramp 는 앞에서 버리는 관측 수.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ANNUALIZATION = 252


def _ramp(s: pd.Series | pd.DataFrame, window: int | None, ramp: int | None):
    r = (window or 0) if ramp is None else ramp
    return s.iloc[r:] if r else s


def _roll(x, window: int | None):
    return x.expanding(min_periods=1) if window is None else x.rolling(window, min_periods=1)


def returns(x: pd.Series, obs: int = 1) -> pd.Series:
    """단순수익률 x_t / x_{t−obs} − 1."""
    return x / x.shift(obs) - 1


def std(x: pd.Series, window: int | None = None, ramp: int | None = 0) -> pd.Series:
    """표본표준편차(ddof=1)."""
    return _ramp(_roll(x, window).std(ddof=1), window, ramp)


def volatility(x: pd.Series, window: int | None = None, ramp: int | None = 0,
               annualization: int = ANNUALIZATION) -> pd.Series:
    """가격 x 의 단순수익률 표준편차 × √252 × 100 (연율화 %)."""
    r = returns(x)
    out = _roll(r, window).std(ddof=1) * np.sqrt(annualization) * 100
    return _ramp(out, window, ramp)


def exponential_std(x: pd.Series, beta: float = 0.75) -> pd.Series:
    """지수가중 표준편차(편향 보정). gs-quant 와 같이 adjust=False: 가중치 (1−β)β^i, 가장 오래된 관측만 β^t."""
    return x.ewm(alpha=1 - beta, adjust=False).std(bias=False)


def exponential_volatility(x: pd.Series, beta: float = 0.75, annualization: int = ANNUALIZATION) -> pd.Series:
    """가격 x 의 수익률 지수가중 변동성, 연율화 %."""
    return exponential_std(returns(x), beta) * np.sqrt(annualization) * 100


def max_drawdown(x: pd.Series, window: int | None = None, ramp: int | None = 0) -> pd.Series:
    """창 안에서의 최대낙폭(0 이하 비율)."""
    peak = _roll(x, window).max()
    dd = x / peak - 1
    return _ramp(_roll(dd, window).min(), window, ramp)


def zscores(x: pd.Series, window: int | None = None, ramp: int | None = 0) -> pd.Series:
    """window=None: 전 구간 평균·표준편차로 표준화(값마다 같은 기준). 정수: rolling 표준화."""
    if window is None:
        return (x - x.mean()) / x.std(ddof=1)
    r = x.rolling(window, min_periods=1)
    return _ramp((x - r.mean()) / r.std(ddof=1), window, ramp)


def beta(x: pd.Series, b: pd.Series, window: int | None = None, ramp: int | None = 0) -> pd.Series:
    """가격 x 의 벤치 b 대비 베타 = cov(rx, rb) / var(rb)."""
    rx, rb = returns(x), returns(b)
    df = pd.concat([rx, rb], axis=1, keys=["x", "b"]).dropna()
    roll = _roll(df, window)
    cov = roll.cov().xs("x", level=1)["b"]
    var = _roll(df["b"], window).var(ddof=1)
    return _ramp(cov / var, window, ramp)
