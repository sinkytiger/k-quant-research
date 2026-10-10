"""대차거래: 응답 파싱(금액 백만원 → 원, 거래 없는 날 제외), 상장주식 수 추정."""
import pandas as pd

from src.data import lending as ld


def test_parse_units_and_skip():
    rows = [{"bsop_date": "20261008", "stck_prpr": "263000.00", "new_stcn": "1000402", "rdmp_stcn": "145168",
             "rmnd_stcn": "82287891", "rmnd_amt": "21641715"},
            {"bsop_date": "20261007", "stck_prpr": "0", "rmnd_stcn": "1", "rmnd_amt": "1"},
            {"bsop_date": "", "stck_prpr": "1"}]
    df = ld.parse(rows)
    assert list(df.index) == [pd.Timestamp("2026-10-08")]
    assert df["lend_amt"].iloc[0] == 21_641_715_000_000          # 21.6조
    assert abs(df["lend_qty"].iloc[0] * 263000 / df["lend_amt"].iloc[0] - 1) < 0.01   # 주수 × 종가 ≈ 금액


def test_save_merges_by_date(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    a = pd.DataFrame({"lend_new": [1.0, 2.0], "lend_rdmp": [0.0, 0.0], "lend_qty": [10.0, 11.0], "lend_amt": [100.0, 110.0]},
                     index=pd.to_datetime(["2026-10-07", "2026-10-08"]))
    ld.save("005930", a)
    ld.save("005930", a.iloc[1:].assign(lend_qty=12.0))
    out = ld.load("005930")
    assert len(out) == 2 and out["lend_qty"].iloc[-1] == 12.0
