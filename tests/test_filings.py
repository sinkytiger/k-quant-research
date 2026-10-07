"""공시 피드: 일상 신고 판별, 등락률 맵(거래정지 제외), 휴장일 공시의 다음 거래일, 압축 행."""
import json

import pandas as pd

from src import filings


def test_is_routine():
    assert filings.is_routine("임원ㆍ주요주주특정증권등소유상황보고서")
    assert filings.is_routine("일괄신고추가서류(파생결합사채-주가연계파생결합사채)")
    assert not filings.is_routine("주요사항보고서(자기주식취득결정)")
    assert not filings.is_routine("단일판매ㆍ공급계약체결")


def test_fluc_map_drops_halted():
    raw = pd.DataFrame({"ISU_CD": ["000001", "000002"], "BAS_DD": ["20261006"] * 2,
                        "FLUC_RT": ["2.50", "0.00"], "ACC_TRDVOL": ["100", "0"]})
    fl = filings.fluc_map(raw)
    assert fl == {("000001", pd.Timestamp("2026-10-06")): 0.025}


def test_reaction_holiday_filing_uses_next_open_day():
    cal = [pd.Timestamp("2026-10-02"), pd.Timestamp("2026-10-06")]
    fl = {("000001", pd.Timestamp("2026-10-02")): 0.01, ("000001", pd.Timestamp("2026-10-06")): -0.02}
    assert filings.reaction("000001", "2026-10-02", cal, fl) == (0.01, -0.02)
    assert filings.reaction("000001", "2026-10-05", cal, fl) == (None, -0.02)  # 대체공휴일 공시
    assert filings.reaction("000001", "2026-10-06", cal, fl) == (-0.02, None)  # 다음 거래일 아직 없음


def test_build_rows_and_write(tmp_path):
    dl = pd.DataFrame({"rcept_no": ["1", "2"], "rcept_dt": pd.to_datetime(["2026-10-02", "2026-10-06"]),
                       "stock_code": ["000001", ""], "corp_name": ["가", "나"],
                       "report_nm": ["주요사항보고서(자기주식취득결정)", "기타"]})
    out = filings.build(dl, [pd.Timestamp("2026-10-02")], {("000001", pd.Timestamp("2026-10-02")): 0.01},
                        {"000001"}, {"000001": "가나다"})
    assert len(out["rows"]) == 1  # 종목코드 없는 공시는 뺀다
    r = out["rows"][0]
    assert r[:3] == ["2026-10-02", "000001", "가나다"] and out["cats"][r[4]] == "주요사항"
    assert r[5:9] == [0, 1, 0.01, None]
    filings.write(tmp_path / "f.js", out, {"from": "x"})
    s = (tmp_path / "f.js").read_text(encoding="utf-8")
    assert json.loads(s[len("window.KQ_FILINGS="):-2])["from"] == "x"
