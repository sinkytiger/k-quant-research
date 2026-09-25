"""통계 함정 (기획서 5장)."""
import numpy as np
import pandas as pd
import pytest

from src.stats import annualize_overlapping, block_bootstrap_means, nw_tstat


def test_overlapping_annualization_uses_252_over_h():
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(0.0004, 0.01, 5000))
    h = 20
    overlapping_h = daily.rolling(h).sum().dropna()  # 중첩 h일 수익률
    ann = annualize_overlapping(overlapping_h.mean(), h)
    assert ann == pytest.approx(daily.mean() * 252, rel=0.1)
    assert overlapping_h.mean() * 252 == pytest.approx(ann * h, rel=1e-9)  # ×252 는 h배 부풀린다


def test_block_bootstrap_block_length_is_horizon():
    x = pd.Series(np.arange(100, dtype=float))
    means = block_bootstrap_means(x, h=10, n_boot=50, seed=1)
    assert means.shape == (50,)
    # 블록 길이 h 가 반영되는지: h=len 이면 원형 한 블록 = 원 표본 평균
    full = block_bootstrap_means(x, h=100, n_boot=5, seed=1)
    np.testing.assert_allclose(full, x.mean())


def test_nw_t_shrinks_for_overlapping_series():
    rng = np.random.default_rng(3)
    daily = pd.Series(rng.normal(0.001, 0.01, 3000))
    h = 20
    ov = daily.rolling(h).sum().dropna()
    naive_t = ov.mean() / (ov.std() / np.sqrt(len(ov)))
    nw_t = nw_tstat(ov, lags=2 * h)
    assert abs(nw_t) < abs(naive_t) / 2
