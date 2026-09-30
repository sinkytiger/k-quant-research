"""배당 포함 총수익: 배당락일(T+2), 원시 종가 기준 수익률, TR 누적."""
import pandas as pd
import pytest

from src.data import dividends
from src.universe import total_return as tr


def test_ex_date_t_plus_2():
    td = pd.DatetimeIndex(["2024-12-24", "2024-12-26", "2024-12-27", "2024-12-30", "2025-01-02",
                           "2025-06-26", "2025-06-27", "2025-06-30", "2025-07-01"])
    assert tr.ex_date("2024-12-31", td) == (pd.Timestamp("2024-12-26"), pd.Timestamp("2024-12-27"))  # 기준일 휴장
    assert tr.ex_date("2025-06-30", td) == (pd.Timestamp("2025-06-26"), pd.Timestamp("2025-06-27"))  # 기준일 거래일
    assert tr.ex_date("2024-12-20", td) == (None, None)


def test_parse_keeps_common_positive_dividends():
    rows = [{"record_date": "20241231", "sht_cd": "005930", "divi_kind": "결산", "per_sto_divi_amt": "363",
             "stk_kind": "보통", "divi_pay_dt": "2025/04/18"},
            {"record_date": "20240930", "sht_cd": "005930", "divi_kind": "분기", "per_sto_divi_amt": "0", "stk_kind": "보통"},
            {"record_date": "20241231", "sht_cd": "005935", "divi_kind": "결산", "per_sto_divi_amt": "364", "stk_kind": "우선"}]
    df = dividends.parse(rows, "005930")
    assert len(df) == 1 and df.loc[0, "dps"] == 363 and df.loc[0, "pay_date"] == "2025-04-18"


def test_tr_adds_yield_on_ex_date_only():
    td = pd.bdate_range("2025-06-23", periods=8)
    close = pd.Series([100, 100, 100, 100, 98, 98, 99, 99], index=td, dtype=float)  # 배당락일(4번째 뒤) 2% 하락
    y = pd.DataFrame({"ex_date": [td[4]], "cum_date": [td[3]], "dps": [2.0], "raw_close": [100.0], "yield": [0.02]})
    out = tr.tr_close(close, y)
    assert out.iloc[3] == pytest.approx(100)
    assert out.iloc[4] == pytest.approx(100)          # 가격 −2% + 배당 2% = 0
    assert out.iloc[-1] / out.iloc[4] == pytest.approx(99 / 98)
