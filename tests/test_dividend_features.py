"""배당 피처: 지급일 전에는 모른다, 분할 환산, 성장률."""
import pandas as pd
import pytest

from src.data import dividends
from src.features import dividend as dv
from src.universe import total_return as tr


def _setup(monkeypatch, divs, raw_close):
    monkeypatch.setattr(dividends, "load", lambda code: divs)
    real = tr.dividend_yields

    def fake_yields(code, td, raw_close_=None):
        return real(code, td, raw_close=raw_close)
    monkeypatch.setattr(tr, "dividend_yields", fake_yields)


def test_dividend_not_known_before_pay_date(monkeypatch):
    td = pd.bdate_range("2024-12-01", "2025-06-30")
    close = pd.Series(100.0, index=td)
    divs = pd.DataFrame({"record_date": [pd.Timestamp("2024-12-31")], "dps": [5.0], "kind": ["결산"], "pay_date": ["2025-04-18"]})
    _setup(monkeypatch, divs, pd.Series(100.0, index=td))
    f = dv.features(close.to_frame("A"))["dy_ttm"]["A"]
    assert f.loc["2025-04-17"] == 0.0                    # 배당락(12/27)·확정 전: 모른다
    assert f.loc["2025-04-18"] == pytest.approx(0.05)    # 지급일부터 안다


def test_split_converted_to_current_share_basis(monkeypatch):
    td = pd.bdate_range("2017-01-02", "2018-12-31")
    adj = pd.Series(2000.0, index=td)                     # 50:1 분할 뒤 기준 수정가
    raw = pd.Series(100000.0, index=td)                   # 분할 전 원시가
    divs = pd.DataFrame({"record_date": [pd.Timestamp("2017-12-31")], "dps": [5000.0], "kind": ["결산"], "pay_date": ["2018-04-20"]})
    _setup(monkeypatch, divs, raw)
    f = dv.features(adj.to_frame("A"))["dy_ttm"]["A"]
    assert f.loc["2018-05-02"] == pytest.approx(5000 * (2000 / 100000) / 2000)  # = 5%, 분할 무관


def test_growth_needs_both_years(monkeypatch):
    td = pd.bdate_range("2023-01-02", "2025-06-30")
    divs = pd.DataFrame({"record_date": pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31"]), "dps": [4.0, 4.0, 6.0],
                         "kind": ["결산"] * 3, "pay_date": ["2023-04-20", "2024-04-19", "2025-04-18"]})
    _setup(monkeypatch, divs, pd.Series(100.0, index=td))
    g = dv.features(pd.Series(100.0, index=td).to_frame("A"))["div_growth"]["A"]
    assert g.loc["2025-05-02"] == pytest.approx(6 / 4 - 1)
    assert pd.isna(g.loc["2023-05-02"])  # 1년 전 TTM 이 없다
