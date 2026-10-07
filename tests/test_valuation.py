"""재무·밸류에이션: 누적→분기 변환(4분기 = 연간 − 3분기 누적), 연결 우선, TTM·PER·PBR·ROE, 응답 파싱."""
import pandas as pd

from src import valuation as v
from src.data import dart_fin as f


def _fin(code="000001", fs="CFS", revs=(10, 25, 45, 70, 12), ni=(1, 3, 6, 10, 2), eq=100.0):
    rows = []
    for i, (rv, n) in enumerate(zip(revs, ni)):
        year, q = (2025, i + 1) if i < 4 else (2026, 1)
        for item, val in (("revenue", rv), ("op", n * 1.5), ("ni", n), ("equity", eq), ("liab", 50.0), ("assets", 150.0)):
            rows.append({"year": year, "q": q, "stock_code": code, "fs": fs, "item": item, "value": float(val),
                         "rcept_dt": pd.Timestamp(year, 3 * q, 28) + pd.Timedelta(days=45)})
    return pd.DataFrame(rows)


def test_cumulative_to_quarterly_and_ttm():
    q = v.quarterly(v.wide(_fin()))
    hist = v.history(q, "000001")
    assert [h[1] for h in hist] == [10, 15, 20, 25, 12]  # 4분기 = 70 − 45, 2026 1분기는 누적 그대로
    m = v.metrics(q, {"000001": 200.0}).loc["000001"]
    assert m["label"] == "26.1Q"
    assert m["rev_ttm"] == 15 + 20 + 25 + 12 and m["ni_ttm"] == 2 + 3 + 4 + 2
    assert abs(m["per"] - 200 / 11) < 1e-9 and abs(m["pbr"] - 2.0) < 1e-9
    assert abs(m["rev_yoy"] - (12 / 10 - 1)) < 1e-9


def test_consolidated_preferred_and_asof():
    fin = pd.concat([_fin(fs="OFS", revs=(1, 2, 3, 4, 5)), _fin(fs="CFS")])
    w = v.wide(fin)
    assert w.loc[("000001", 2026 * 4 + 1), "revenue"] == 12  # 연결이 있으면 연결
    w2 = v.wide(_fin(), asof="2025-09-01")  # 그때까지 접수된 보고서만
    assert w2.index.get_level_values("period").max() == 2025 * 4 + 2


def test_parse_multi_uses_cumulative_for_half_and_q3():
    items = [{"corp_code": "1", "stock_code": "000001", "rcept_no": "20260814000001", "fs_div": "CFS", "sj_div": "IS",
              "account_nm": "매출액", "thstrm_amount": "171", "thstrm_add_amount": "305"},
             {"corp_code": "1", "stock_code": "000001", "rcept_no": "20260814000001", "fs_div": "CFS", "sj_div": "BS",
              "account_nm": "자본총계", "thstrm_amount": "1,000", "thstrm_add_amount": None},
             {"corp_code": "1", "stock_code": "000001", "rcept_no": "20260814000001", "fs_div": "CFS", "sj_div": "IS",
              "account_nm": "당기순이익(손실)", "thstrm_amount": "7", "thstrm_add_amount": "9"},
             {"corp_code": "1", "stock_code": "000001", "rcept_no": "20260814000001", "fs_div": "CFS", "sj_div": "IS",
              "account_nm": "당기순이익(손실)", "thstrm_amount": "7", "thstrm_add_amount": "9"}]
    d = f.parse_multi(items, "11012").set_index("item")["value"]
    assert d["revenue"] == 305 and d["equity"] == 1000 and d["ni"] == 9 and len(d) == 3
