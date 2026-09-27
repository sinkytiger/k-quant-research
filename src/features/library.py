"""횡단면 피처. 모두 t 시점 종가까지의 정보만 쓴다(미래 값 금지, 테스트로 고정).

예전 코드는 사라졌고 기획서엔 이름만 남아 있어서, 정의는 새로 세웠다(재구성):
- mom_12_1   : 12개월 수익률에서 최근 1개월 제외  close[t-21] / close[t-252] - 1
- rev_1m     : 최근 1개월 수익률 (단기 반전 확인용)       close[t] / close[t-21] - 1
- rel_60     : 60일 수익률 (횡단면에서는 시장 대비 상대강도와 순위가 같다)
- trend_120  : 120일 이동평균 대비 괴리                 close / MA120 - 1
- low_vol    : 60일 일간 수익률 표준편차의 음수 (낮을수록 높은 점수)
- tail       : 60일 중 최악 일간 수익률 (덜 빠진 종목이 높은 점수)
- size       : log(시가총액) (직전 거래일 스냅샷)
- flow_<주체>: 20일 누적 (순매수 ÷ 20일 거래대금 중앙값), 공표 시차 lag=1
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data import flows as flows_mod
from src.features.ic import daily_returns

FLOW_WINDOW = 20


def price_features(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """close: 휴장일 행이 빠진 거래일 × 종목 패널."""
    close = close.dropna(how="all")
    ret = daily_returns(close)
    ma120 = close.rolling(120, min_periods=100).mean()
    return {
        "mom_12_1": close.shift(21) / close.shift(252) - 1,
        "rev_1m": close / close.shift(21) - 1,
        "rel_60": close / close.shift(60) - 1,
        "trend_120": close / ma120 - 1,
        "low_vol": -ret.rolling(60, min_periods=50).std(),
        "tail": ret.rolling(60, min_periods=50).min(),
    }


def size_feature(market_cap: pd.DataFrame) -> pd.DataFrame:
    return np.log(market_cap.where(market_cap > 0))


def flow_features(flow_panels: dict[str, pd.DataFrame], value: pd.DataFrame,
                  window: int = FLOW_WINDOW) -> dict[str, pd.DataFrame]:
    """flow_panels: {주체: 날짜×종목 순매수(원)}. value: 거래대금 패널(원)."""
    out = {}
    for who, fp in flow_panels.items():
        norm = flows_mod.normalized_flow(fp.reindex(index=value.index, columns=value.columns), value)
        out[f"flow_{who}"] = norm.rolling(window, min_periods=window // 2).sum()
    return out
