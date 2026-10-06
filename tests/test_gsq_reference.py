"""src/monitor/gsq.py 를 gs-quant(원본)과 값으로 대조한다.

gs-quant 가 설치된 환경에서만 돈다(기본 환경은 건너뜀):
    C:\\KQuantData\\venv-gsq\\Scripts\\python -m pytest tests/test_gsq_reference.py
"""
import importlib.util
import sys
import types

import numpy as np
import pandas as pd
import pytest

if importlib.util.find_spec("gs_quant") is None:
    pytest.skip("gs-quant 없음 (C:\\KQuantData\\venv-gsq 에서 실행)", allow_module_level=True)

# gs_quant.timeseries 는 import 시점에 statsmodels 를 불러오는데, 이 PC 는 Windows 앱 제어 정책이
# statsmodels 의 DLL 을 막는다(2026-10-07). 대조하는 함수들은 statsmodels 를 쓰지 않으므로
# 이 테스트 안에서만 빈 모듈로 대신한다. 쓰면 AttributeError 로 바로 드러난다.
try:
    import statsmodels.api  # noqa: F401
except ImportError:
    for name in ("statsmodels", "statsmodels.api", "statsmodels.tsa", "statsmodels.tsa.seasonal",
                 "statsmodels.tsa.tsatools", "statsmodels.regression", "statsmodels.regression.rolling"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["statsmodels.regression.rolling"].RollingOLS = None

import gs_quant.timeseries as ts  # noqa: E402

from src.monitor import gsq  # noqa: E402

Window = ts.Window


def _px(n=400, seed=7, vol=0.02):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(10_000 * np.exp(np.cumsum(rng.normal(0, vol, n))), index=idx)


def _same(a: pd.Series, b: pd.Series, rtol=1e-9):
    a, b = a.dropna(), b.dropna()
    common = a.index.intersection(b.index)
    assert len(common) > 0.8 * min(len(a), len(b)), (len(a), len(b), len(common))
    np.testing.assert_allclose(a.loc[common].to_numpy(float), b.loc[common].to_numpy(float), rtol=rtol, atol=1e-12)


X, B = _px(), _px(seed=8, vol=0.015)


def test_returns():
    _same(gsq.returns(X), ts.returns(X))


@pytest.mark.parametrize("w", [22, 63])
def test_volatility(w):
    _same(gsq.volatility(X, w), ts.volatility(X, Window(w, 0)))


def test_volatility_ramp():
    _same(gsq.volatility(X, 22, ramp=22), ts.volatility(X, Window(22, 22)))


@pytest.mark.parametrize("w", [None, 60])
def test_max_drawdown(w):
    _same(gsq.max_drawdown(X, w), ts.max_drawdown(X, Window(w, 0)))


@pytest.mark.parametrize("w", [None, 30])
def test_zscores(w):
    _same(gsq.zscores(X, w), ts.zscores(X, Window(w, 0) if w else None) if w else ts.zscores(X))


@pytest.mark.parametrize("beta", [0.75, 0.94])
def test_exponential_volatility(beta):
    _same(gsq.exponential_volatility(X, beta), ts.exponential_volatility(X, beta))


def test_beta():
    _same(gsq.beta(X, B, 60), ts.beta(X, B, Window(60, 0)), rtol=1e-7)
