"""프로그램 매매: 응답 파싱(시장 백만원 → 원, 종목 원), 만기일(둘째 목요일, 휴장이면 앞 거래일)."""
import pandas as pd

from src.data import program as pg


def test_parse_market_units():
    rows = [{"stck_bsop_date": "20261008", "arbt_smtn_ntby_tr_pbmn": "-264905", "nabt_smtn_ntby_tr_pbmn": "-946751",
             "whol_smtn_ntby_tr_pbmn": "-1211656", "whol_smtn_shnu_tr_pbmn": "8118310", "whol_smtn_seln_tr_pbmn": "9329966",
             "whol_ntby_tr_pbmn_rate": "-4.97"}]
    df = pg.parse_market(rows)
    assert df["net"].iloc[0] == -1_211_656_000_000                   # −1.21조
    assert df["arb_net"].iloc[0] + df["nonarb_net"].iloc[0] == df["net"].iloc[0]
    assert df["buy"].iloc[0] - df["sell"].iloc[0] == df["net"].iloc[0]


def test_parse_stock_skips_untraded():
    rows = [{"stck_bsop_date": "20261008", "stck_clpr": "263000", "whol_smtn_ntby_tr_pbmn": "-798879097750",
             "whol_smtn_shnu_tr_pbmn": "1", "whol_smtn_seln_tr_pbmn": "2", "acml_tr_pbmn": "3"},
            {"stck_bsop_date": "20261007", "stck_clpr": "0"}]
    df = pg.parse_stock(rows)
    assert len(df) == 1 and df["net"].iloc[0] == -798_879_097_750


def test_expiry_second_thursday_and_holiday():
    assert pg.expiry(2026, 12) == pd.Timestamp("2026-12-10")
    assert pg.expiry(2026, 10) == pd.Timestamp("2026-10-08")
    cal = pd.Series({pd.Timestamp("2026-12-10"): False, pd.Timestamp("2026-12-09"): True})
    assert pg.expiry(2026, 12, cal) == pd.Timestamp("2026-12-09")
    e = pg.next_expiries("2026-10-11")
    assert e["monthly"] == pd.Timestamp("2026-11-12") and e["quarterly"] == pd.Timestamp("2026-12-10")
    assert pg.next_expiries("2026-12-10")["quarterly"] == pd.Timestamp("2026-12-10")
