"""해외 지수·시장별 투자자 파싱 (실제 응답 필드)."""
import pandas as pd

from src.data import market_extra as mx


def test_parse_global_drops_empty_and_sorts():
    rows = [{"stck_bsop_date": "20260930", "ovrs_nmix_prpr": "26861.06", "ovrs_nmix_oprc": "26800", "ovrs_nmix_hgpr": "26900", "ovrs_nmix_lwpr": "26700"},
            {"stck_bsop_date": "20260929", "ovrs_nmix_prpr": "26700.00", "ovrs_nmix_oprc": "", "ovrs_nmix_hgpr": "", "ovrs_nmix_lwpr": ""},
            {"stck_bsop_date": "", "ovrs_nmix_prpr": "0"}]
    df = mx.parse_global(rows)
    assert list(df.index) == [pd.Timestamp("2026-09-29"), pd.Timestamp("2026-09-30")]
    assert df.loc["2026-09-30", "Close"] == 26861.06


def test_parse_investor_converts_million_won():
    rows = [{"stck_bsop_date": "20260930", "bstp_nmix_prpr": "6838.04", "prsn_ntby_tr_pbmn": "1210896",
             "frgn_ntby_tr_pbmn": "-2064557", "orgn_ntby_tr_pbmn": "-799251"}]
    df = mx.parse_investor(rows)
    assert df.loc["2026-09-30", "외국인"] == -2064557 * 1_000_000
    assert df.loc["2026-09-30", "index"] == 6838.04


def test_parse_calendar_open_flag():
    rows = [{"bass_dt": "20261005", "opnd_yn": "N"}, {"bass_dt": "20261006", "opnd_yn": "Y"}, {"bass_dt": ""}]
    cal = mx.parse_calendar(rows)
    assert list(cal["open"]) == [False, True]


def test_trading_lag_skips_holidays():
    cal = pd.Series({pd.Timestamp("2026-10-05"): False, pd.Timestamp("2026-10-06"): True, pd.Timestamp("2026-10-07"): True})
    # 10-02(금) 데이터, 10-07 아침: 주말·대체공휴일 빼면 10-06 하루 → 정상 범위
    assert mx.trading_lag("2026-10-02", "2026-10-07 08:50", cal) == 1
    assert mx.trading_lag("2026-10-06", "2026-10-07", cal) == 0
    # 달력이 없으면 평일 − 알려진 휴장일
    assert mx.trading_lag("2026-10-02", "2026-10-07", pd.Series(dtype=bool)) == 2
    assert mx.trading_lag("2026-10-02", "2026-10-07", pd.Series(dtype=bool), {pd.Timestamp("2026-10-05")}) == 1
