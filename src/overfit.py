"""여러 번 시도한 뒤 고른 전략의 샤프를 깎아 보는 도구 (docs/research_principles.md 3장).

- PSR (Bailey & López de Prado 2012): 관측 샤프가 기준 샤프 SR* 보다 클 확률.
  왜도·첨도와 표본 길이를 반영한다.
- 기대 최대 샤프 (Bailey & López de Prado 2014): 진짜 샤프가 0인 전략 N개를 시도했을 때
  우연히 나오는 최대 샤프의 기댓값.
- DSR = PSR(SR* = 기대 최대 샤프). 시도 횟수 N 만큼 기준을 올린 PSR.

샤프는 전부 **관측 주기 단위**(일간 수익률이면 일간 샤프)다. 연율화 샤프를 넣지 않는다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def sharpe(r: pd.Series) -> float:
    r = pd.Series(r).dropna()
    return float(r.mean() / r.std(ddof=1))


def psr(r: pd.Series, sr_star: float = 0.0) -> float:
    """P(진짜 샤프 > sr_star). r: 주기 수익률."""
    r = pd.Series(r).dropna()
    n = len(r)
    if n < 3:
        return float("nan")
    sr = sharpe(r)
    skew = float(stats.skew(r, bias=False))
    kurt = float(stats.kurtosis(r, fisher=False, bias=False))  # 정규분포 = 3
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return float("nan")
    z = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """진짜 샤프 0 인 N 개 시도의 최대 샤프 기댓값. sr_var: 시도들 샤프의 분산."""
    if n_trials < 2:
        return 0.0
    z1 = stats.norm.ppf(1 - 1 / n_trials)
    z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_var) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def dsr(r: pd.Series, trial_sharpes: list[float] | np.ndarray) -> float:
    """디플레이티드 샤프: 고른 전략 r 과, 시도한 모든 전략의 샤프(같은 주기 단위)."""
    t = np.asarray(trial_sharpes, dtype=float)
    t = t[np.isfinite(t)]
    if len(t) < 2:
        return psr(r, 0.0)
    return psr(r, expected_max_sharpe(len(t), float(t.var(ddof=1))))


def bonferroni_t(n_tests: int, alpha: float = 0.05) -> float:
    """양측 본페로니 t 기준선 (정규 근사). 22개면 약 3.06."""
    return float(stats.norm.ppf(1 - alpha / (2 * n_tests)))
