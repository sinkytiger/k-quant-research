"""PSR·DSR·기대 최대 샤프."""
import numpy as np
import pandas as pd
import pytest

from src import overfit


def _normal(sr, n=2520, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(sr * 0.01, 0.01, n))


def test_psr_is_half_at_observed_sharpe():
    r = _normal(0.05)
    assert overfit.psr(r, overfit.sharpe(r)) == pytest.approx(0.5)


def test_psr_high_for_strong_strategy_low_for_noise():
    assert overfit.psr(_normal(0.1)) > 0.99
    assert 0.05 < overfit.psr(_normal(0.0, seed=3)) < 0.95


def test_expected_max_sharpe_grows_with_trials():
    v = 0.0004  # 일간 샤프 표준편차 0.02
    e10, e100, e1000 = (overfit.expected_max_sharpe(n, v) for n in (10, 100, 1000))
    assert 0 < e10 < e100 < e1000
    # 1,000번 시도면 진짜 샤프 0 이어도 기대 최대 ≈ 3.25σ
    assert e1000 / np.sqrt(v) == pytest.approx(3.25, abs=0.05)


def test_dsr_penalizes_many_trials():
    r = _normal(0.04)
    few = overfit.dsr(r, np.random.default_rng(1).normal(0, 0.02, 3))
    many = overfit.dsr(r, np.random.default_rng(1).normal(0, 0.02, 500))
    assert many < few < overfit.psr(r) + 1e-12


def test_bonferroni_matches_research_note():
    assert overfit.bonferroni_t(22) == pytest.approx(3.06, abs=0.01)
    assert overfit.bonferroni_t(16) == pytest.approx(2.96, abs=0.01)
