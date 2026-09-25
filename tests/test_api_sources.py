"""KRX Open API 스냅샷 → 수정주가, KIS 수급 (네트워크 없음)."""
import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src import kis, krx_api
from src.data import flows
from src.universe import krx_daily, marketcap, prices


def _row(day, code, close, chg, open_=None, vol=100, cap=1_000, shares=10, name="X"):
    open_ = close if open_ is None else open_
    return {"BAS_DD": day, "ISU_CD": code, "ISU_NM": name, "TDD_CLSPRC": str(close),
            "CMPPREVDD_PRC": str(chg), "TDD_OPNPRC": str(open_), "TDD_HGPRC": str(open_ and close),
            "TDD_LWPRC": str(open_ and close), "ACC_TRDVOL": str(vol), "ACC_TRDVAL": "1,000",
            "MKTCAP": str(cap), "LIST_SHRS": str(shares)}


# ---------- 수정주가 ----------
def test_split_is_adjusted_from_krx_base_price():
    """삼성전자 2018-05-04 50:1 분할 실제 값."""
    raw = pd.DataFrame([
        _row("20180502", "005930", 2_650_000, 0, vol=10),
        _row("20180503", "005930", 2_650_000, 0, open_=0, vol=0),  # 분할 전 거래정지
        _row("20180504", "005930", 51_900, -1_100, open_=53_000, vol=39_565_391),
    ])
    adj = krx_daily.adjust(krx_daily.tidy_stock(raw))
    assert adj.loc["2018-05-04", "Close"] == 51_900  # 최신 값은 원시 그대로
    assert adj.loc["2018-05-02", "Close"] == pytest.approx(53_000)
    assert adj.loc["2018-05-02", "Volume"] == 500  # 거래량은 반대로 50배
    assert pd.isna(adj.loc["2018-05-03", "Open"])  # 거래정지일
    r = adj["Close"].pct_change()
    assert r.loc["2018-05-04"] == pytest.approx(51_900 / 53_000 - 1)


def test_normal_days_have_factor_one_and_returns_match_fluc():
    raw = pd.DataFrame([_row("20200102", "A", 1000, 0), _row("20200103", "A", 1100, 100),
                        _row("20200106", "A", 990, -110)])
    adj = krx_daily.adjust(krx_daily.tidy_stock(raw))
    assert (adj["factor"] == 1).all()
    assert list(adj["Close"]) == [1000, 1100, 990]


def test_reused_code_after_long_gap_is_not_chained():
    raw = pd.DataFrame([_row("20150102", "A", 1000, 0), _row("20200102", "A", 50, 10)])
    adj = krx_daily.adjust(krx_daily.tidy_stock(raw))
    assert adj.loc["2015-01-02", "Close"] == 1000


def test_build_prices_marketcap_and_names(tmp_data):
    raw = pd.DataFrame([_row("20200102", "000060", 100, 0, cap=500, name="메리츠화재"),
                        _row("20200102", "005930", 55_000, 0, cap=900),
                        _row("20200103", "005930", 56_000, 1_000, cap=950, name="삼성전자")])
    tidy = krx_daily.tidy_stock(raw)
    built = krx_daily.build_prices(tidy)
    assert built == {"000060": 1, "005930": 2}
    assert prices.load("005930")["source"].iloc[-1] == "krx_api"
    assert krx_daily.build_marketcap(tidy) == 2
    assert marketcap.load("2020-01-03").loc["005930", "market_cap"] == 950
    assert krx_daily.names_from(tidy) == {"000060": "메리츠화재", "005930": "삼성전자"}


# ---------- KRX Open API ----------
@pytest.fixture
def krx_env(tmp_data, monkeypatch):
    monkeypatch.setenv("KRX_API_KEY", "test-key")
    monkeypatch.setattr(krx_api, "MIN_INTERVAL", 0)
    return tmp_data


def test_holiday_empty_block_recorded_and_skipped(krx_env, monkeypatch):
    calls = []

    def fake_get(url, params, headers):
        calls.append(params["basDd"])
        assert url.startswith("https://") and headers["AUTH_KEY"] == "test-key"
        return {"OutBlock_1": [] if params["basDd"] == "20200101" else [_row(params["basDd"], "A", 1, 0)]}

    monkeypatch.setattr(krx_api, "_get", fake_get)
    assert krx_daily.collect_day("stk", "2020-01-01") == "holiday"
    assert krx_daily.collect_day("stk", "2020-01-02") == "saved"
    assert krx_daily.collect_day("stk", "2020-01-02") == "exists"
    assert pd.Timestamp("2020-01-01") not in krx_daily.candidate_days("2019-12-31", "2020-01-03")
    assert calls == ["20200101", "20200102"]


def test_etf_blank_rows_on_holiday_are_not_saved(krx_env, monkeypatch):
    """ETF API 는 휴장일에 가격 칸이 빈 행을 준다. 거래일이 아니다."""
    blank = {"BAS_DD": "20150928", "ISU_CD": "069500", "TDD_CLSPRC": "", "CMPPREVDD_PRC": ""}
    monkeypatch.setattr(krx_api, "_get", lambda *a: {"OutBlock_1": [blank]})
    assert krx_daily.collect_day("etf", "2015-09-28") == "missing"
    assert krx_daily.saved_days("etf") == []
    assert krx_daily.load_holidays() == set()  # 휴장 판정은 stk 로만


def test_only_calendar_dataset_records_holidays(krx_env, monkeypatch):
    monkeypatch.setattr(krx_api, "_get", lambda *a: {"OutBlock_1": []})
    assert krx_daily.collect_day("idx_kospi", "2020-01-02") == "missing"  # 일시적 빈 응답일 수 있다
    assert krx_daily.load_holidays() == set()
    assert krx_daily.collect_day("stk", "2020-01-01") == "holiday"
    assert krx_daily.collect_day("stk", "2020-01-01") == "holiday"
    assert krx_daily.holidays_path().read_text(encoding="utf-8").split() == ["20200101"]  # 중복 기록 없음


def test_401_means_service_not_subscribed(krx_env, monkeypatch):
    monkeypatch.setattr(krx_api, "_get", lambda *a: {"respCode": "401", "respMsg": "Unauthorized"})
    with pytest.raises(krx_api.KrxApiError, match="이용신청"):
        krx_api.fetch(krx_api.ETF, "20200102")


def test_daily_quota_guard(krx_env, monkeypatch):
    monkeypatch.setattr(krx_api, "_get", lambda *a: {"OutBlock_1": []})
    monkeypatch.setattr(krx_api, "DAILY_BUDGET", 2)
    krx_api.fetch(krx_api.STOCK_KOSPI, "20200102")
    krx_api.fetch(krx_api.STOCK_KOSPI, "20200103")
    with pytest.raises(krx_api.QuotaExceeded):
        krx_api.fetch(krx_api.STOCK_KOSPI, "20200106")


def test_bench_from_index_and_etf_snapshots(tmp_data):
    idx = pd.DataFrame([{"BAS_DD": d, "IDX_NM": n, "OPNPRC_IDX": "1", "HGPRC_IDX": "1", "LWPRC_IDX": "1",
                         "CLSPRC_IDX": c, "ACC_TRDVOL": "1"} for d, n, c in
                        [("20200102", "코스피", "2,175.17"), ("20200102", "코스피 200", "290.1")]])
    p = krx_daily.snap_path("idx_kospi", "2020-01-02")
    p.parent.mkdir(parents=True)
    idx.to_csv(p, index=False, encoding="utf-8-sig")
    out = krx_daily.build_bench()
    assert out == {"KOSPI": 1, "KOSPI200": 1}
    assert prices.load_bench("KOSPI")["Close"].iloc[0] == pytest.approx(2175.17)


# ---------- KIS ----------
@pytest.fixture
def kis_env(tmp_data, monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ENV", "prod")
    monkeypatch.setattr(kis, "MIN_INTERVAL", {"prod": 0, "vts": 0})
    return tmp_data


def test_kis_token_is_cached(kis_env, monkeypatch):
    issued = []

    def post(url, body):
        issued.append(url)
        exp = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        return {"access_token": f"t{len(issued)}", "access_token_token_expired": exp}

    monkeypatch.setattr(kis, "_post", post)
    assert kis.token() == "t1"
    assert kis.token() == "t1"  # 1분 1회 발급 제한 → 캐시 재사용
    assert kis.token(now=datetime.now() + timedelta(hours=23, minutes=30)) == "t2"  # 만료 임박이면 재발급
    assert len(issued) == 2


def test_kis_flow_fields_and_units():
    """KIS 실제 응답(2026-09-23 삼성전자) 필드. 외국인합계 = 등록+비등록, 금액은 백만원."""
    row = {"stck_bsop_date": "20260923", "orgn_ntby_tr_pbmn": "382751", "etc_corp_ntby_tr_pbmn": "500000",
           "prsn_ntby_tr_pbmn": "-2167606", "frgn_ntby_tr_pbmn": "1283306"}
    df = flows.parse_kis([row, {"stck_bsop_date": ""}])  # 빈 날짜 행은 버린다
    assert len(df) == 1
    assert df.loc["2026-09-23", "개인"] == -2_167_606 * 1_000_000
    assert df.loc["2026-09-23", "외국인합계"] == 1_283_306 * 1_000_000


def test_kis_error_raises(kis_env, monkeypatch):
    monkeypatch.setattr(kis, "token", lambda now=None: "t")
    monkeypatch.setattr(kis, "_get", lambda url, h, p: ({"rt_cd": "1", "msg_cd": "EGW", "msg1": "오류"}, {}))
    with pytest.raises(kis.KisError):
        kis.investor_daily("005930")
