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
