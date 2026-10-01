"""데이터 품질 점검 (대시보드 개요 탭). 순수 함수만 두고 입력은 호출하는 쪽이 만든다.

각 점검은 Issue 목록을 돌려준다. severity: critical > serious > warning > info, ok 는 문제 없음.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

LIMIT = 0.305  # 가격제한폭 ±30% (+ 호가 반올림 여유)


@dataclass
class Issue:
    check: str
    severity: str  # critical | serious | warning | info | ok
    summary: str
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def tidy_returns(tidy: pd.DataFrame) -> pd.DataFrame:
    """일별매매 tidy(Date, code, name, Close, Chg, Volume, Value) → 종목별 일간 수익률·조정계수·첫 거래일 여부."""
    t = tidy.sort_values(["code", "Date"]).copy()
    prev = t.groupby("code")["Close"].shift(1)
    base = t["Close"] - t["Chg"]
    t["ret"] = t["Close"] / base - 1
    t["factor"] = (base / prev).where(prev > 0)
    t["first_day"] = prev.isna()
    return t


def price_jumps(t: pd.DataFrame, universe: set | None = None, limit: float = LIMIT) -> Issue:
    """하루 수익률이 가격제한폭(±30%) 밖. 스냅샷 첫날(신규상장·재상장)은 제외.

    정리매매(상장폐지 전 7거래일)는 가격제한이 없어서 실제로 일어난다 → 유니버스 밖 종목은 경고,
    유니버스 안 종목이면 연구에 영향을 줄 수 있어 심각.
    """
    bad = t[(t["ret"].abs() > limit) & ~t["first_day"] & (t["Volume"] > 0)].sort_values("Date")
    uni = universe or set()
    inside = bad[bad["code"].isin(uni)]
    items = [f"{r.Date:%m-%d} {r.name}({r.code}) {r.ret:+.1%}" + (" [유니버스]" if r.code in uni else "")
             for r in bad.itertuples()]
    sev = "serious" if len(inside) else ("warning" if len(bad) else "ok")
    summary = (f"{len(bad)}건 (유니버스 안 {len(inside)}건)" if len(bad) else "없음")
    return Issue("가격제한폭 밖 수익률 (정리매매 또는 데이터 오류)", sev, summary, items[:30])


def adjustments(t: pd.DataFrame, tol: float = 1e-6) -> Issue:
    """분할·병합·권리락 등으로 기준가가 조정된 날 (정상 동작 확인용)."""
    adj = t[(t["factor"] - 1).abs() > tol]
    items = [f"{r.Date:%m-%d} {r.name}({r.code}) 계수 {r.factor:.4f}" for r in adj.sort_values("Date").itertuples()]
    return Issue("수정주가 조정 (분할·병합 등)", "info" if items else "ok", f"{len(items)}건" if items else "없음", items[:30])


def flow_exceeds_value(net: dict[str, pd.DataFrame], value: pd.DataFrame, names: dict[str, str]) -> Issue:
    """투자자별 순매수 절댓값이 그날 거래대금보다 크면 불가능한 값."""
    items = []
    for who, f in net.items():
        f2 = f.reindex(index=value.index, columns=value.columns)
        bad = (f2.abs() > value * 1.001) & value.notna() & (value > 0)
        for d, c in zip(*bad.to_numpy().nonzero()):
            dt, code = value.index[d], value.columns[c]
            items.append(f"{dt:%m-%d} {names.get(code, code)}({code}) {who} {f2.iat[d, c] / 1e8:+,.0f}억 > 거래대금 {value.iat[d, c] / 1e8:,.0f}억")
    return Issue("수급 > 거래대금", "serious" if items else "ok", f"{len(items)}건" if items else "없음", items[:30])


def holiday_conflicts(holidays: set, stk_days: set) -> Issue:
    both = sorted(holidays & stk_days)
    items = [f"{d:%Y-%m-%d}" for d in both]
    return Issue("휴장일 모순 (휴장 기록 + 데이터 있음)", "critical" if items else "ok", f"{len(items)}일" if items else "없음", items)


def missing_days(calendar: list, saved: dict[str, set]) -> Issue:
    """거래일(stk 기준)인데 다른 데이터셋이 비어 있는 날."""
    items = []
    for ds, days in saved.items():
        miss = [d for d in calendar if d not in days]
        if miss:
            items.append(f"{ds}: " + ", ".join(f"{d:%m-%d}" for d in miss[-10:]) + (f" 외 {len(miss) - 10}일" if len(miss) > 10 else ""))
    return Issue("스냅샷 누락일", "warning" if items else "ok", f"{len(items)}개 데이터셋" if items else "없음", items)


def stale(last_dates: dict[str, pd.Timestamp], asof: pd.Timestamp, calendar: pd.DatetimeIndex, names: dict[str, str],
          max_lag: int, check: str, severity: str) -> Issue:
    """마지막 날짜가 asof 보다 max_lag 거래일 넘게 뒤처진 종목. 기록이 아예 없으면 '없음'."""
    cal = pd.DatetimeIndex(calendar)
    items = []
    for code, last in sorted(last_dates.items()):
        if last is None or pd.isna(last):
            items.append(f"{names.get(code, code)}({code}) 기록 없음")
            continue
        lag = int(((cal > last) & (cal <= asof)).sum())
        if lag > max_lag:
            items.append(f"{names.get(code, code)}({code}) {lag}거래일 ({last:%m-%d} 이후 없음)")
    return Issue(check, severity if items else "ok", f"{len(items)}종목" if items else "없음", items[:30])
