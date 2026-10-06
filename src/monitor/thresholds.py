"""급등·급락 임계선 (복구 기획서 6-1장).

- 일간 수익률의 EWMA σ (β=0.94, RiskMetrics)
- 임계선_t = clip(k × σ_{t−1}, floor, cap), k=2.0, floor 2.5%, cap 15%
  σ 는 **어제까지**의 값만 쓴다(오늘 수익률이 자기 임계선을 키우지 않게).
- 관측이 MIN_OBS(60) 미만이면 NaN (판단하지 않는다).
"""
from __future__ import annotations

import pandas as pd

from src.monitor import gsq

BETA = 0.94
K = 2.0
FLOOR = 0.025
CAP = 0.15
MIN_OBS = 60


def ewma_sigma(close: pd.Series, beta: float = BETA) -> pd.Series:
    """일간(비연율화) EWMA σ, 비율. gsq.exponential_volatility 와 같은 정의를 일간 비율로 되돌린 것."""
    return gsq.exponential_std(gsq.returns(close), beta)


def threshold(close: pd.Series, k: float = K, floor: float = FLOOR, cap: float = CAP,
              min_obs: int = MIN_OBS, beta: float = BETA) -> pd.Series:
    sig = ewma_sigma(close, beta).shift(1)
    thr = (k * sig).clip(lower=floor, upper=cap)
    n_obs = gsq.returns(close).notna().cumsum().shift(1)
    return thr.where(n_obs >= min_obs)


def moves(close: pd.Series, **kw) -> pd.DataFrame:
    """(ret, thr, surge, plunge). surge: ret > thr, plunge: ret < −thr. 임계선이 없으면 False."""
    ret = gsq.returns(close)
    thr = threshold(close, **kw)
    return pd.DataFrame({
        "ret": ret, "thr": thr,
        "surge": (ret > thr).fillna(False), "plunge": (ret < -thr).fillna(False),
    })
