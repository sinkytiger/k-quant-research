"""KIS 뉴스 제목 페이징: 커서 이동, 중복 제거, since 에서 멈춤, 같은 초 40건 초과."""
import pandas as pd

from src import kis
from src.data import news


def _fake_stream(times):
    """times: 기사 시각 목록. KIS 처럼 '기준 시각 이전(포함) 최신 40건'을 돌려준다."""
    arts = [{"cntt_usiq_srno": f"N{i:05d}", "data_dt": t.strftime("%Y%m%d"), "data_tm": t.strftime("%H%M%S"),
             "dorg": "테스트", "news_ofer_entp_code": "1", "news_lrdv_code": "", "hts_pbnt_titl_cntt": f"제목 {i}",
             "iscd1": "005930", "iscd2": "000660"} for i, t in enumerate(times)]
    calls = []

    def call(tr, path, params):
        assert tr == news.TR
        cur = pd.Timestamp(params["FID_INPUT_DATE_1"][2:] + " " + params["FID_INPUT_HOUR_1"])
        calls.append(cur)
        rows = sorted([a for a in arts if pd.Timestamp(a["data_dt"] + " " + a["data_tm"]) <= cur],
                      key=lambda a: (a["data_dt"], a["data_tm"]), reverse=True)[:40]
        return {"rt_cd": "0", "output": rows}, {}
    return call, calls


def test_pages_backwards_until_since_without_duplicates(monkeypatch):
    times = list(pd.date_range("2026-09-01", "2026-09-10", freq="37min"))
    call, calls = _fake_stream(times)
    monkeypatch.setattr(kis, "call", call)
    df = news.fetch("005930", "2026-09-03", until="2026-09-10 00:00:00")
    expect = [t for t in times if pd.Timestamp("2026-09-03") <= t <= pd.Timestamp("2026-09-10")]
    assert len(df) == len(expect) and df["id"].is_unique
    assert df["dt"].min() >= pd.Timestamp("2026-09-03")
    assert df.iloc[0]["codes"] == "005930 000660"


def test_more_than_40_articles_in_one_second_does_not_loop_forever(monkeypatch):
    burst = [pd.Timestamp("2026-09-05 09:00:00")] * 55
    older = list(pd.date_range("2026-09-04 09:00", periods=10, freq="h"))
    call, calls = _fake_stream(older + burst)
    monkeypatch.setattr(kis, "call", call)
    df = news.fetch("005930", "2026-09-04", until="2026-09-05 10:00:00")
    assert len(df) == 40 + 10  # 같은 초 55건 중 API 가 주는 40건 + 이전 10건 (나머지 15건은 API 한계)
    assert len(calls) < 10


def test_save_merges_by_id(tmp_data):
    df = news.parse([{"cntt_usiq_srno": "A1", "data_dt": "20260929", "data_tm": "141500", "dorg": "이투데이",
                      "hts_pbnt_titl_cntt": "원화 약세", "iscd1": "005930"}])
    news.save("005930", df)
    news.save("005930", df)
    back = news.load("005930")
    assert len(back) == 1 and back.iloc[0]["title"] == "원화 약세"
    assert news.update_since("005930") == pd.Timestamp("2026-09-29 08:15:00")
