"""외국인 지분율: 응답 파싱, 일·주·월 합칠 때 같은 날짜는 일별 우선, 한도 소진율 → 지분율, 기간 변화."""
import pandas as pd

from src.data import foreign as fx


def _rows(pairs):
    return [{"stck_bsop_date": d, "hts_frgn_ehrt": str(v), "stck_clpr": "100"} for d, v in pairs]


def test_parse_skips_empty_and_untraded():
    rows = _rows([("20261008", 46.35), ("20261007", 46.4)]) + [{"stck_bsop_date": "20261006", "hts_frgn_ehrt": "", "stck_clpr": "100"},
                                                              {"stck_bsop_date": "20261005", "hts_frgn_ehrt": "1", "stck_clpr": "0"}]
    df = fx.parse(rows, "D")
    assert list(df.index) == [pd.Timestamp("2026-10-07"), pd.Timestamp("2026-10-08")]
    assert df["ehrt"].iloc[-1] == 46.35


def test_merge_prefers_daily_on_same_date():
    d = fx.parse(_rows([("20261008", 46.35)]), "D")
    m = fx.parse(_rows([("20261008", 40.0), ("20260930", 45.0)]), "M")
    out = fx.merge(m, d)
    assert out.loc["2026-10-08", "ehrt"] == 46.35 and out.loc["2026-10-08", "freq"] == "D"
    assert len(out) == 2


def test_limit_from_holdings():
    # 대한항공 (2026-10-09): 보유 9,806만 주 = 상장주식의 26.63%, 소진율 53.27% → 한도 약 50%
    assert abs(fx.limit_from(26.63, 100, 53.27) - 49.99) < 0.05
    assert fx.limit_from(46.35, 100, 46.35) == 100.0   # 한도 없음 (삼성전자)
    assert fx.limit_from(49.0, 100, 100.0) == 49.0      # 한도 꽉 참 (KT)
    assert fx.limit_from(1, 0, 10) is None and fx.limit_from(1, 10, 0) is None


def test_load_converts_exhaustion_to_ownership(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    fx.save("003490", fx.parse(_rows([("20261008", 53.27)]), "D"))
    df = fx.load("003490", {"003490": 50.0})
    assert df["ratio"].iloc[-1] == 26.635 and df["ehrt"].iloc[-1] == 53.27 and df.attrs["limit"] == 50.0
    assert fx.load("003490", {})["ratio"].iloc[-1] == 53.27   # 한도 모르면 그대로


def test_change_uses_last_value_before_window():
    df = fx.load("x", {})  # 빈 표
    assert fx.change(df, 28) is None
    df = fx.parse(_rows([("20260901", 40.0), ("20260915", 41.0), ("20261008", 43.5)]), "D")
    assert fx.change(df, 28, "ehrt") == 3.5   # 9/10 이전 마지막 = 9/1 값 40
    assert fx.change(df, 365, "ehrt") is None
