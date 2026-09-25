"""수집 모듈과 pykrx 함정 (가짜 백엔드, 네트워크 없음)."""
import numpy as np
import pandas as pd
import pytest

from src import krx
from src.data import flows
from src.universe import kospi200, marketcap, prices


# ---------- pykrx 함정 ----------
def test_empty_dataframe_instead_of_list_does_not_crash(fake_krx):
    fake_krx.get_index_portfolio_deposit_file = lambda *a, **k: pd.DataFrame()
    assert kospi200.fetch_members(pd.Timestamp("2020-01-01")) == []


def test_is_empty_handles_dataframe_list_none():
    assert krx.is_empty(None) and krx.is_empty([]) and krx.is_empty(pd.DataFrame())
    assert not krx.is_empty(["005930"])


def test_fetch_members_zero_pads_and_dedups(fake_krx):
    fake_krx.get_index_portfolio_deposit_file = lambda *a, **k: [5930, "000660", "005930"]
    assert kospi200.fetch_members(pd.Timestamp("2020-01-01")) == ["000660", "005930"]


def test_empty_snapshot_is_never_saved(tmp_data):
    with pytest.raises(ValueError):
        kospi200.save_snapshot(pd.Timestamp("2020-01-01"), [])


# ---------- 종목명 ----------
def test_save_names_does_not_overwrite_with_failure_value(tmp_data):
    kospi200.save_names({"005930": "삼성전자"})
    merged = kospi200.save_names({"005930": "005930", "000660": "000660"})
    assert merged["005930"] == "삼성전자"
    assert merged["000660"] == "000660"
    merged = kospi200.save_names({"000660": "SK하이닉스"})
    assert kospi200.load_names() == {"000660": "SK하이닉스", "005930": "삼성전자"}


def test_bulk_names_fallback(fake_krx, tmp_data):
    df = pd.DataFrame({"종목명": ["삼성전자"]}, index=["005930"])
    fake_krx.get_market_price_change = lambda *a, **k: df

    def broken(code):
        raise AttributeError("'NoneType' object")

    fake_krx.get_market_ticker_name = broken
    assert kospi200.fetch_names(["005930", "000060"]) == {"005930": "삼성전자", "000060": "000060"}


# ---------- 시세 ----------
def _krx_ohlcv(dates, closes):
    return pd.DataFrame({"시가": closes, "고가": closes, "저가": closes, "종가": closes,
                         "거래량": [100] * len(closes), "등락률": 0.0}, index=pd.DatetimeIndex(dates, name="날짜"))


def test_clean_maps_korean_columns_drops_zero_close_and_dups():
    d = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-03", "2020-01-06"])
    raw = _krx_ohlcv(d, [100, 101, 102, 0])
    out = prices.clean(raw)
    assert list(out.columns) == prices.COLS
    assert list(out.index) == list(pd.to_datetime(["2020-01-02", "2020-01-03"]))
    assert out.loc["2020-01-03", "Close"] == 102  # 중복은 마지막 값


def test_clean_flattens_yfinance_multiindex():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03"]).tz_localize("Asia/Seoul")
    cols = pd.MultiIndex.from_product([["Close", "High", "Low", "Open", "Volume"], ["005930.KS"]])
    raw = pd.DataFrame(np.ones((2, 5)), index=idx, columns=cols)
    out = prices.clean(raw)
    assert list(out.columns) == prices.COLS and out.index.tz is None


def test_fetch_any_falls_back_to_krx_for_delisted(fake_krx, tmp_data, monkeypatch):
    def yf_empty(*a, **k):
        raise ValueError("yfinance 빈 응답")

    monkeypatch.setattr(prices, "fetch_yf", yf_empty)
    fake_krx.get_market_ohlcv_by_date = lambda a, b, code, adjusted=True: _krx_ohlcv(
        pd.bdate_range("2020-01-02", periods=3), [10, 11, 12])
    df, src = prices.fetch_any("000060", "2020-01-01")
    assert src == "krx" and len(df) == 3


def test_update_refetches_full_history_when_adjustment_changes(fake_krx, tmp_data, monkeypatch):
    monkeypatch.setattr(prices, "fetch_yf", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    days = pd.bdate_range("2020-01-01", periods=30)
    calls = []

    def ohlcv(a, b, code, adjusted=True):
        calls.append(a)
        scale = 0.5 if len(calls) > 1 else 1.0  # 두 번째 호출부터 분할로 과거가 절반
        sel = days[(days >= pd.Timestamp(a)) & (days <= pd.Timestamp(b))]
        return _krx_ohlcv(sel, [100.0 * scale] * len(sel))

    fake_krx.get_market_ohlcv_by_date = ohlcv
    prices.backfill("000001", "2020-01-01", krx_only=True)
    monkeypatch.setattr(prices, "_today", lambda: days[-1])
    df, _ = prices.update("000001", "2020-01-01")
    assert (df["Close"] == 50.0).all()  # 이어붙이지 않고 전체를 새 기준으로 다시 받았다
    assert len(calls) == 3


def test_delisted_guess(tmp_data):
    today = pd.Timestamp("2026-09-01")
    prices.save("AAA", prices.clean(_krx_ohlcv(pd.bdate_range(end="2026-08-31", periods=21), [1.0] * 21)), "yf")
    prices.save("BBB", prices.clean(_krx_ohlcv(pd.bdate_range("2020-01-01", periods=5), [1.0] * 5)), "krx")
    got = [c for c, _ in prices.delisted_guess(["AAA", "BBB"], today=today)]
    assert got == ["BBB"]


# ---------- 시가총액 ----------
def _cap_table(value):
    return pd.DataFrame({"종가": [100, 200], "시가총액": [value, value], "거래량": [1, 1],
                         "거래대금": [1, 1], "상장주식수": [1, 1]}, index=["005930", "000660"])


def test_marketcap_zero_table_is_error_and_retries_previous_business_day(fake_krx):
    asked = []

    def get_market_cap(d, market="KOSPI"):
        asked.append(d)
        return _cap_table(0 if d == "20240103" else 10)  # 수요일이 휴장(0 표)

    fake_krx.get_market_cap = get_market_cap
    with pytest.raises(ValueError):
        marketcap.validate(_cap_table(0))
    actual, df = marketcap.fetch_cap("2024-01-03")
    assert actual == pd.Timestamp("2024-01-02") and asked == ["20240103", "20240102"]
    # 토요일 요청은 호출도 없이 금요일로 간다
    asked.clear()
    actual, _ = marketcap.fetch_cap("2024-01-06")
    assert actual == pd.Timestamp("2024-01-05") and asked == ["20240105"]


def test_marketcap_gives_up_after_five_business_days(fake_krx):
    fake_krx.get_market_cap = lambda d, market="KOSPI": _cap_table(0)
    with pytest.raises(ValueError):
        marketcap.fetch_cap("2024-01-10")


def test_zero_file_counts_as_missing(tmp_data):
    good = marketcap.validate(_cap_table(10))
    marketcap.save("2024-01-02", good)
    bad = good.copy()
    bad["market_cap"] = 0
    marketcap.save("2024-01-03", bad)
    assert marketcap.saved_days() == [pd.Timestamp("2024-01-02")]
    assert [p.stem for p in marketcap.invalid_files()] == ["20240103"]


# ---------- 수급 ----------
def test_flows_fetch_chunks_and_keeps_investor_columns(fake_krx, tmp_data):
    calls = []

    def tv(a, b, code, on="순매수"):
        assert on == "순매수"
        calls.append((a, b))
        d = pd.bdate_range(a, b)[:2]
        return pd.DataFrame({"기관합계": 1, "기타법인": 2, "개인": -3, "외국인합계": 0, "전체": 0}, index=d)

    fake_krx.get_market_trading_value_by_date = tv
    df = flows.fetch("005930", "2020-01-01", "2022-06-30")
    assert len(calls) == 3
    assert list(df.columns) == flows.FLOW_COLS
    flows.save("005930", df)
    assert flows.load("005930").shape == df.shape
