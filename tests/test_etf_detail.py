"""ETF 상세: 괴리율, 자금 흐름(분할일 0), 추적 차이(레버리지 제외), 정수 가격 압축."""
import pandas as pd

from src import etf_detail as ed


def _g(n=6, idx=None, shares=None):
    d = pd.bdate_range("2026-01-01", periods=n)
    close = [100.0 + i for i in range(n)]
    return pd.DataFrame({"Date": d, "code": "X", "index_name": "코스피 200", "close": close, "chg": [0] + [1.0] * (n - 1),
                         "nav": [c - 0.5 for c in close], "shares": shares or [1000.0] * n,
                         "idx": idx or [10.0 + i * 0.1 for i in range(n)], "aum": 1.0, "value": 1.0})


def test_premium_flow_and_compact_prices():
    g = _g(shares=[1000, 1100, 1100, 2500, 2500, 2400])
    out = ed.one(g, days=5)
    assert out["close"][-1] == 105 and isinstance(out["close"][-1], int)
    assert abs(out["prem_now"] - (105 / 104.5 - 1)) < 1e-5
    # 2배 넘게 늘어난 날(1100→2500)은 분할로 보고 0, 마지막 날 −100주 × NAV 104.5
    assert out["flow"][2] == 0 and abs(out["flow"][-1] - (-100 * 104.5 / 1e8)) < 0.05


def test_tracking_difference_and_leveraged():
    g = _g(n=70)
    t = ed.one(g, days=65)["track_3m"]
    assert t is not None and abs(t["diff"] - (t["etf"] - t["idx"])) < 1e-4
    assert ed.one(g, days=65, leveraged=True)["track_3m"] is None


def test_newly_listed_etf_is_skipped():
    assert ed.one(_g(n=1), days=250) == {} and ed.one(_g(n=2), days=250) == {}
    assert ed.one(_g(n=3), days=250)["close"]
