"""공매도·신용잔고 파싱 (실제 응답 필드): 단위(신용 금액 만원 → 원), 패딩 행 제외, 날짜 = 매매일."""
from src.data import short_credit as sc


def test_parse_short():
    rows = [{"stck_bsop_date": "20261007", "stck_clpr": "268500", "ssts_cntg_qty": "706028", "ssts_vol_rlim": "4.31",
             "ssts_tr_pbmn": "192302240750", "ssts_tr_pbmn_rlim": "4.30"},
            {"stck_bsop_date": "", "stck_clpr": "0"}]
    df = sc.parse_short(rows)
    assert len(df) == 1 and df.iloc[0]["short_amt_pct"] == 4.30 and df.iloc[0]["short_qty"] == 706028


def test_parse_credit_units_and_date():
    rows = [{"deal_date": "20261002", "stlm_date": "20261007", "stck_prpr": "276000", "whol_loan_rmnd_stcn": "21532530",
             "whol_loan_rmnd_amt": "473125700", "whol_loan_rmnd_rate": "0.36", "whol_loan_gvrt": "5.75"}]
    df = sc.parse_credit(rows)
    assert str(df.index[0].date()) == "2026-10-02"  # 결제일이 아니라 매매일
    assert df.iloc[0]["loan_amt"] == 473125700 * 10000 and df.iloc[0]["loan_rate"] == 0.36
