"""통계 도구. 함정:

- 중첩 h일 수익률의 평균을 연율화할 때는 ×(252/h). ×252 는 h배 부풀린다.
- 블록 부트스트랩 블록 길이 = 지평 h. 행 단위(종목-일) 부트스트랩은 금지:
  같은 날 종목끼리는 독립이 아니다. 날짜 단위 시계열(예: 일별 IC, 일별 스프레드)에만 쓴다.
- Newey-West t 의 lag 는 2h.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def annualize_overlapping(mean_h_return: float, h: int) -> float:
    return float(mean_h_return) * (252.0 / h)


def block_bootstrap_means(series: pd.Series, h: int, n_boot: int = 2000, seed: int = 0) -> np.ndarray:
    """날짜 순서 시계열에 대한 원형(circular) 블록 부트스트랩. 블록 길이 = h."""
    x = pd.Series(series).dropna().to_numpy(dtype=float)
    n = len(x)
    if n == 0:
        return np.array([])
    block = max(1, int(h))
    n_blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    idx = idx.reshape(n_boot, -1)[:, :n]
    return x[idx].mean(axis=1)


def nw_tstat(series: pd.Series, lags: int) -> float:
    """평균의 Newey-West t 값 (Bartlett 커널)."""
    x = pd.Series(series).dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, min(lags, n - 1) + 1):
        w = 1 - k / (lags + 1)
        var += 2 * w * (e[k:] @ e[:-k]) / n
    if var <= 0:
        return float("nan")
    return float(x.mean() / np.sqrt(var / n))
