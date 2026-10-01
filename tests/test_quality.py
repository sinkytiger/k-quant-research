"""데이터 품질 점검: 가격제한폭 밖 수익률, 첫날 제외, 수급 > 거래대금, 휴장일 모순, 끊김."""
import pandas as pd

from src import quality as q


def _tidy(rows):
    return pd.DataFrame(rows, columns=["Date", "code", "name", "Close", "Chg", "Volume"])


def test_price_jump_flags_beyond_limit_but_not_first_day():
    d = pd.bdate_range("2026-09-01", periods=3)
    t = q.tidy_returns(_tidy([
        (d[0], "A", "정상", 100.0, 0.0, 10), (d[1], "A", "정상", 129.0, 29.0, 10),
        (d[0], "B", "오류", 100.0, 0.0, 10), (d[1], "B", "오류", 150.0, 50.0, 10),
        (d[2], "C", "신규상장", 300.0, 200.0, 10),
    ]))
    iss = q.price_jumps(t)
    assert iss.severity == "warning" and len(iss.items) == 1 and "B" in iss.items[0]  # 유니버스 밖: 정리매매일 수 있다
    assert q.price_jumps(t, universe={"B"}).severity == "serious"                    # 유니버스 안이면 심각


def test_adjustment_detected_from_base_price():
    d = pd.bdate_range("2018-05-03", periods=2)
    t = q.tidy_returns(_tidy([(d[0], "S", "삼성", 2650000.0, 0.0, 10), (d[1], "S", "삼성", 51900.0, -1100.0, 10)]))
    iss = q.adjustments(t)
    assert iss.severity == "info" and "0.0200" in iss.items[0]
    assert q.price_jumps(t).severity == "ok"  # 분할은 급변이 아니다(기준가 대비 −2%)


def test_flow_exceeds_value():
    idx = pd.bdate_range("2026-09-01", periods=2)
    value = pd.DataFrame({"A": [1e9, 1e9]}, index=idx)
    net = {"외국인": pd.DataFrame({"A": [5e8, 3e9]}, index=idx)}
    iss = q.flow_exceeds_value(net, value, {"A": "에이"})
    assert iss.severity == "serious" and len(iss.items) == 1 and "09-02" in iss.items[0]


def test_holiday_conflict_and_missing_days_and_stale():
    h = {pd.Timestamp("2026-09-24")}
    assert q.holiday_conflicts(h, {pd.Timestamp("2026-09-24")}).severity == "critical"
    assert q.holiday_conflicts(h, {pd.Timestamp("2026-09-23")}).severity == "ok"
    cal = list(pd.bdate_range("2026-09-21", periods=5))
    assert q.missing_days(cal, {"etf": set(cal[:-1])}).severity == "warning"
    s = q.stale({"A": pd.Timestamp("2026-09-21"), "B": cal[-1], "C": None}, cal[-1], pd.DatetimeIndex(cal), {}, 3, "끊김", "warning")
    assert s.severity == "warning" and len(s.items) == 2  # A(4거래일), C(기록 없음)
