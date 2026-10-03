"""종목 상세: 수익률·52주 위치, 거래정지일 처리, 수급 합계, 시황 기사 표시, 임원 보고 제외, 배당수익률, 파일 쓰기."""
import json

import numpy as np
import pandas as pd
import pytest

from src import stock_detail as sd


def test_price_stats_returns_and_52w():
    idx = pd.bdate_range("2024-01-01", periods=300)
    close = pd.Series(np.linspace(100, 199.67, 300), index=idx)
    st = sd.price_stats(close)
    assert st["ret_1m"] == pytest.approx(close.iloc[-1] / close.iloc[-22] - 1, abs=1e-4)
    assert st["ret_1y"] == pytest.approx(close.iloc[-1] / close.iloc[-251] - 1, abs=1e-4)
    assert st["pos_52w"] == pytest.approx(1.0)
    assert st["hi_52w"] == pytest.approx(close.iloc[-1], abs=0.01)


def test_ohlcv_halt_day_uses_close():
    idx = pd.bdate_range("2024-01-01", periods=2)
    px = pd.DataFrame({"Open": [0, 10], "High": [0, 12], "Low": [0, 9], "Close": [11, 11], "Volume": [0, 5]}, index=idx)
    rows = sd.ohlcv(px)
    assert rows[0] == ["240101", 11, 11, 11, 11, 0]
    assert rows[1] == ["240102", 10, 12, 9, 11, 5]


def test_flow_block_sums():
    idx = pd.bdate_range("2024-01-01", periods=70)
    fl = pd.DataFrame({"외국인합계": 1.0, "기관합계": -2.0, "개인": 1.0, "기타법인": 0.0}, index=idx)
    b = sd.flow_block(fl)
    assert len(b["dates"]) == 60
    assert b["sums"]["frgn_20"] == 20 and b["sums"]["inst_60"] == -120
    assert sd.flow_block(pd.DataFrame()) == {}


def test_news_block_marks_market_auto():
    nw = pd.DataFrame({"id": ["1", "2"], "dt": pd.to_datetime(["2024-01-02 09:00", "2024-01-02 10:00"]),
                       "source": ["a", "b"], "title": ["삼성전자(005930) 상승률 +3.17%, 3거래일 연속 상승", "실적 호조로 사상 최대"]})
    out = sd.news_block(nw)
    assert out[0]["title"].startswith("실적")  # 최신이 먼저
    assert out[1]["auto"] is True and out[1]["tone"] == 0


def test_dart_block_drops_insider_reports():
    dl = pd.DataFrame({"rcept_no": ["1", "2", "3"], "rcept_dt": pd.to_datetime(["2024-01-02"] * 3),
                       "report_nm": ["임원ㆍ주요주주특정증권등소유상황보고서", "주요사항보고서(자기주식취득결정)", "임원ㆍ주요주주특정증권등소유상황보고서"]})
    b = sd.dart_block(dl)
    assert b["insider"] == 2
    assert [r["cat"] for r in b["rows"]] == ["주요사항"]
    assert b["rows"][0]["url"].endswith("rcpNo=2")


def test_dividend_block_ttm_yield():
    dv = pd.DataFrame({"record_date": pd.to_datetime(["2023-06-30", "2023-12-31", "2024-06-30"]),
                       "dps": [100.0, 300.0, 200.0], "kind": ["분기", "결산", "분기"], "pay_date": ["2023-08-20", "2024-04-17", None]})
    b = sd.dividend_block(dv, 10_000, pd.Timestamp("2024-07-01"))
    assert b["dps_ttm"] == 500  # 2023-06-30 은 1년보다 이전
    assert b["dy_ttm"] == pytest.approx(0.05)
    assert b["rows"][0]["date"] == "2024-06-30" and b["rows"][0]["pay"] is None


def test_write_all_removes_stale_and_skips_unchanged(tmp_path):
    assert sd.write_all(tmp_path, {"000001": {"a": 1}, "000002": {"a": 2}}) == 2
    assert sd.write_all(tmp_path, {"000001": {"a": 1}}) == 0
    assert [f.name for f in tmp_path.glob("*.js")] == ["000001.js"]
    s = (tmp_path / "000001.js").read_text(encoding="utf-8")
    assert json.loads(s.split("]=", 1)[1].rstrip(";\n")) == {"a": 1}
