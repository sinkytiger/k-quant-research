"""뉴스 감성: 긴 표현 우선 매칭, 15:30 컷오프, 전재 중복, 피처가 미래 기사를 쓰지 않음."""
import pandas as pd
import pytest

from src.features import news_sentiment as ns


def test_longest_match_first_and_neutral_consumes():
    assert ns.score_title("목표가 하향") == (0, 1, -1.0)          # '하향'만 한 번, '목표가 상향' 아님
    assert ns.score_title("최대주주 변경")[:2] == (0, 0)           # '최대'로 잡히지 않는다
    assert ns.score_title("어닝 서프라이즈에 신고가") == (2, 0, 1.0)
    assert ns.score_title("실적 기대 이하, 주가 급락") == (0, 2, -1.0)  # '기대'(긍정)로 잡히지 않는다
    assert ns.score_title("수주 증가에도 우려") == (2, 1, pytest.approx(1 / 3))
    assert ns.score_title("오늘의 공시 요약") == (0, 0, 0.0)


def test_cutoff_1530_and_holidays_go_to_next_trading_day():
    td = pd.DatetimeIndex(["2026-09-23", "2026-09-28", "2026-09-29"])  # 9/24~27 추석·주말
    dt = pd.Series(pd.to_datetime(["2026-09-23 15:29:59", "2026-09-23 15:30:00", "2026-09-26 10:00:00",
                                   "2026-09-29 16:00:00"]))
    got = ns.assign_trade_date(dt, td)
    assert list(got[:3]) == [pd.Timestamp("2026-09-23"), pd.Timestamp("2026-09-28"), pd.Timestamp("2026-09-28")]
    assert pd.isna(got.iloc[3])  # 마지막 거래일 이후 → 아직 배정 불가


def test_syndicated_duplicates_counted_once():
    td = pd.bdate_range("2026-09-01", periods=5)
    a = pd.DataFrame({"dt": pd.to_datetime(["2026-09-01 09:00", "2026-09-01 09:05", "2026-09-01 10:00"]),
                      "title": ["삼성전자 급등", "삼성전자  급등", "삼성전자 수주"]})
    t = ns.daily_table(a, td)
    assert t.loc["2026-09-01", "n"] == 2 and t.loc["2026-09-01", "score_sum"] == 2.0


def test_features_do_not_use_future_articles():
    td = pd.bdate_range("2026-01-01", periods=120)
    rows = [{"dt": d + pd.Timedelta(hours=10), "title": "주가 상승"} for d in td[:100]]
    base = ns.features({"A": ns.daily_table(pd.DataFrame(rows), td)}, td)
    rows2 = rows + [{"dt": td[101] + pd.Timedelta(hours=10), "title": "주가 폭락 급락 쇼크"}] * 5
    after = ns.features({"A": ns.daily_table(pd.DataFrame(rows2), td)}, td)
    t = td[100]
    for k in base:
        assert base[k].loc[:t, "A"].equals(after[k].loc[:t, "A"])
    assert after["sent_5d"].loc[td[101], "A"] < base["sent_5d"].loc[td[101], "A"]


def test_market_auto_headlines_are_excluded():
    auto = ["[장중수급포착] KCC, 외국인 6일 연속 순매수행진... 주가 +3.71%", "<유>LG전자우, 상한가 진입.. +29.99% ↑",
            "외국계 순매수,도 상위종목(코스피) 금액기준", "[특징주] 한미반도체, 급등", "삼성전기(-5.73%) 등 순매도"]
    keep = ["CJ대한통운 1분기 영업이익 921억원…작년 동기 대비 증가", "실적 랠리로 '코스피 5000' 간다", "HBM4 황금수율 잡고 물량전"]
    assert all(ns.is_market_auto(t) for t in auto)
    assert not any(ns.is_market_auto(t) for t in keep)
    td = pd.bdate_range("2026-09-01", periods=3)
    a = pd.DataFrame({"dt": pd.to_datetime(["2026-09-01 09:00:00", "2026-09-01 09:10:00"]), "title": [auto[3], keep[0]]})
    assert ns.daily_table(a, td).loc["2026-09-01", "n"] == 1
