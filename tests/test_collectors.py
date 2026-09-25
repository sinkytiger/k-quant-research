"""저장소·유니버스·수급 페이징 (네트워크 없음)."""
import numpy as np
import pandas as pd
import pytest

from src import kis
from src.data import flows
from src.universe import marketcap, membership, prices


# ---------- 시점별 유니버스 (시총 상위 200) ----------
def test_common_stock_filter():
    assert membership.is_common_stock("005930", "삼성전자")
    assert not membership.is_common_stock("005935", "삼성전자우")  # 우선주
    assert not membership.is_common_stock("088980", "맥쿼리인프라")
    assert not membership.is_common_stock("330590", "롯데리츠")
    assert not membership.is_common_stock("0126Z5", "신규우선")


def test_select_members_top_n_with_buffer():
    cap = pd.Series({f"{i:05d}0": float(1000 - i) for i in range(30)})  # 000000 이 1위
    top = membership.select_members(cap, {}, None, top_n=10, buffer_n=12)
    assert top == sorted(f"{i:05d}0" for i in range(10))
    # 기존 편입 11위(000100)는 버퍼 12위 안이라 유지, 대신 10위(000090)가 못 들어온다
    prev = frozenset(top) - {"000090"} | {"000100"}
    again = membership.select_members(cap, {}, prev, top_n=10, buffer_n=12)
    assert "000100" in again and "000090" not in again and len(again) == 10
    # 버퍼 밖(13위)이면 빠진다
    prev2 = frozenset(top) - {"000090"} | {"000120"}
    assert "000120" not in membership.select_members(cap, {}, prev2, top_n=10, buffer_n=12)


def test_build_uses_only_caps_before_snapshot_date(tmp_data):
    def cap_file(day, caps):
        marketcap.save(day, pd.DataFrame({"close": 1, "market_cap": caps, "volume": 1, "value": 1, "shares": 1},
                                         index=pd.Index(list(caps.index), name="code")))

    cap_file("2024-01-31", pd.Series({"000010": 10.0, "000020": 5.0}))
    cap_file("2024-02-01", pd.Series({"000010": 1.0, "000020": 50.0}))  # 2월 1일 당일 값은 쓰면 안 된다
    built = membership.build("2024-02-01", "2024-02-01", top_n=1, buffer_n=1)
    assert built == {pd.Timestamp("2024-02-01"): 1}
    assert membership.members_asof("2024-02-15") == frozenset({"000010"})  # 1/31 시총 기준


def test_empty_snapshot_is_never_saved(tmp_data):
    with pytest.raises(ValueError):
        membership.save_snapshot(pd.Timestamp("2020-01-01"), [])


# ---------- 종목명 ----------
def test_save_names_does_not_overwrite_with_failure_value(tmp_data):
    membership.save_names({"005930": "삼성전자"})
    merged = membership.save_names({"005930": "005930", "000660": "000660"})
    assert merged["005930"] == "삼성전자"
    assert merged["000660"] == "000660"
    membership.save_names({"000660": "SK하이닉스"})
    assert membership.load_names() == {"000660": "SK하이닉스", "005930": "삼성전자"}


# ---------- 시세 저장소 ----------
def _ohlcv(dates, closes):
    return pd.DataFrame({"시가": closes, "고가": closes, "저가": closes, "종가": closes,
                         "거래량": [100] * len(closes)}, index=pd.DatetimeIndex(dates, name="날짜"))


def test_clean_maps_korean_columns_drops_zero_close_and_dups():
    d = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-03", "2020-01-06"])
    out = prices.clean(_ohlcv(d, [100, 101, 102, 0]))
    assert list(out.columns) == prices.COLS
    assert list(out.index) == list(pd.to_datetime(["2020-01-02", "2020-01-03"]))
    assert out.loc["2020-01-03", "Close"] == 102  # 중복은 마지막 값


def test_clean_flattens_yfinance_multiindex():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03"]).tz_localize("America/New_York")
    cols = pd.MultiIndex.from_product([["Close", "High", "Low", "Open", "Volume"], ["AAPL"]])
    out = prices.clean(pd.DataFrame(np.ones((2, 5)), index=idx, columns=cols))
    assert list(out.columns) == prices.COLS and out.index.tz is None


def test_yf_update_refetches_full_history_when_adjustment_changes(tmp_data, monkeypatch):
    days = pd.bdate_range("2020-01-01", periods=30)
    calls = []

    def fake_yf(ticker, start, end=None):
        calls.append(start)
        scale = 0.5 if len(calls) > 1 else 1.0  # 두 번째 호출부터 분할로 과거가 절반
        sel = days[days >= pd.Timestamp(start)]
        return prices.clean(_ohlcv(sel, [100.0 * scale] * len(sel)))

    monkeypatch.setattr(prices, "fetch_yf", fake_yf)
    prices.backfill_yf("AAPL", "2020-01-01")
    df = prices.update_yf("AAPL", "2020-01-01")
    assert (df["Close"] == 50.0).all() and len(df) == 30  # 이어붙이지 않고 새 기준으로 다시 받았다
    assert len(calls) == 3


def test_delisted_guess(tmp_data):
    today = pd.Timestamp("2026-09-01")
    prices.save("AAA", prices.clean(_ohlcv(pd.bdate_range(end="2026-08-31", periods=21), [1.0] * 21)), "krx_api")
    prices.save("BBB", prices.clean(_ohlcv(pd.bdate_range("2020-01-01", periods=5), [1.0] * 5)), "krx_api")
    assert [c for c, _ in prices.delisted_guess(["AAA", "BBB"], today=today)] == ["BBB"]


# ---------- 시가총액 ----------
def test_zero_file_counts_as_missing(tmp_data):
    good = pd.DataFrame({"close": [1, 2], "market_cap": [10, 20], "volume": 1, "value": 1, "shares": 1},
                        index=pd.Index(["005930", "000660"], name="code"))
    marketcap.save("2024-01-02", good)
    bad = good.copy()
    bad["market_cap"] = 0
    marketcap.save("2024-01-03", bad)
    assert marketcap.saved_days() == [pd.Timestamp("2024-01-02")]
    assert [p.stem for p in marketcap.invalid_files()] == ["20240103"]


# ---------- 수급 (KIS 날짜 지정 페이징) ----------
def _kis_rows(end, n=30):
    days = pd.bdate_range(end=end, periods=n)[::-1]  # KIS 는 최신일이 먼저
    return [{"stck_bsop_date": f"{d:%Y%m%d}", "orgn_ntby_tr_pbmn": "1", "etc_corp_ntby_tr_pbmn": "2",
             "prsn_ntby_tr_pbmn": "-3", "frgn_ntby_tr_pbmn": "0"} for d in days]


def test_flows_page_backwards_until_start(tmp_data, monkeypatch):
    asked = []

    def call(tr, path, params):
        assert tr == flows.KIS_TR
        asked.append(params["FID_INPUT_DATE_1"])
        return {"rt_cd": "0", "output2": _kis_rows(params["FID_INPUT_DATE_1"])}, {}

    monkeypatch.setattr(kis, "call", call)
    df = flows.fetch("005930", "2020-01-01", "2020-04-30")
    assert df.index.min() >= pd.Timestamp("2020-01-01") and df.index.max() == pd.Timestamp("2020-04-30")
    assert not df.index.duplicated().any()
    assert len(asked) == 3  # 영업일 87일 → 30거래일씩 거꾸로 3페이지
    assert df.loc["2020-04-30", "기타법인"] == 2 * flows.KIS_UNIT  # 백만원 → 원
    assert list(df.columns) == flows.FLOW_COLS


def test_flows_stops_before_listing(tmp_data, monkeypatch):
    listed = pd.Timestamp("2020-03-02")

    def call(tr, path, params):
        rows = [r for r in _kis_rows(params["FID_INPUT_DATE_1"]) if pd.Timestamp(r["stck_bsop_date"]) >= listed]
        return {"rt_cd": "0", "output2": rows}, {}

    monkeypatch.setattr(kis, "call", call)
    df = flows.fetch("NEW000", "2016-01-01", "2020-04-30")
    assert df.index.min() == listed


def test_flows_never_ask_today_before_cutoff():
    """KIS 는 15:40 전 당일 날짜 조회를 막는다(OPSQ2001)."""
    assert flows.last_complete_day(pd.Timestamp("2026-09-25 15:16")) == pd.Timestamp("2026-09-24")
    assert flows.last_complete_day(pd.Timestamp("2026-09-25 15:40")) == pd.Timestamp("2026-09-25")


def test_flows_save_marks_source(tmp_data):
    df = flows.parse_kis(_kis_rows("2020-01-31", 3))
    flows.save("005930", df)
    back = flows.load("005930")
    assert back[flows.FLOW_COLS].shape == df.shape and (back["source"] == "kis").all()
