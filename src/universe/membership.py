"""시점별 유니버스: KOSPI 보통주 시가총액 상위 200 (생존편향 방지 1단계).

실제 KOSPI200 과거 구성종목은 공식 API 어디에도 없다. 그래서 KRX Open API 일별매매
스냅샷(그날 상장돼 있던 전 종목, 이후 상폐된 종목 포함)의 시가총액으로 직접 만든다.

규칙
- 매월 1일자 스냅샷. 그 날짜 **직전** 거래일의 시가총액만 쓴다(당일·미래 값 금지).
- 보통주만: 단축코드 끝자리 '0' (우선주는 5·7·9·K 등), 리츠·인프라펀드·스팩·투자회사 제외.
- 버퍼: 기존 편입 종목은 순위 220위 안이면 유지, 빈자리를 순위순으로 채워 200개.
  KOSPI200 처럼 경계 종목이 매달 들락거리지 않게 한다.
- KOSPI200 과 차이: 업종 대표성 규칙·유동성 심사·정기변경 일정(6·12월)이 없다.

저장: membership/<YYYYMMDD>.csv (code 한 열). 각 날짜에는 그 날짜 이전 스냅샷 중
가장 최근 것만 쓴다(members_asof). 미래 스냅샷은 절대 보지 않는다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

from src import config

TOP_N = 200
BUFFER_N = 220
EXCLUDE_NAME = re.compile(r"리츠|REIT|인프라|스팩|기업인수목적|투자회사|선박투자|유전")


def month_starts(start: str, end: str | None = None) -> list[pd.Timestamp]:
    end_ts = pd.Timestamp(end) if end else pd.Timestamp(date.today())
    return list(pd.date_range(pd.Timestamp(start), end_ts, freq="MS"))


def is_common_stock(code: str, name: str | None = None) -> bool:
    code = str(code)
    if len(code) != 6 or not code.endswith("0"):
        return False
    return not (isinstance(name, str) and EXCLUDE_NAME.search(name))


def select_members(cap: pd.Series, names: dict[str, str], prev: frozenset[str] | None,
                   top_n: int = TOP_N, buffer_n: int = BUFFER_N) -> list[str]:
    """cap: 종목코드 → 시가총액 (직전 거래일). prev: 직전 스냅샷."""
    cap = cap[(cap.fillna(0) > 0)]
    cap = cap[[is_common_stock(c, names.get(c)) for c in cap.index]]
    ranked = list(cap.sort_values(ascending=False).index)
    rank = {c: i + 1 for i, c in enumerate(ranked)}
    keep = [c for c in (prev or ()) if rank.get(c, 10**9) <= buffer_n]
    keep = sorted(keep, key=rank.get)[:top_n]
    chosen = set(keep)
    for c in ranked:
        if len(chosen) >= top_n:
            break
        chosen.add(c)
    return sorted(chosen)


def snapshot_path(asof: pd.Timestamp) -> Path:
    return config.MEMBERSHIP_DIR / f"{pd.Timestamp(asof):%Y%m%d}.csv"


def save_snapshot(asof: pd.Timestamp, codes: list[str]) -> Path:
    if len(codes) == 0:
        raise ValueError(f"빈 스냅샷은 저장하지 않는다: {asof:%Y-%m-%d}")
    p = snapshot_path(asof)
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"code": codes}).to_csv(p, index=False)
    return p


def build(start: str = "2016-01-01", end: str | None = None,
          top_n: int = TOP_N, buffer_n: int = BUFFER_N) -> dict[pd.Timestamp, int]:
    """시가총액 스냅샷(market_cap/)으로 월초 유니버스를 처음부터 다시 만든다. {날짜: 종목수}"""
    from src.universe import marketcap

    days = marketcap.saved_days()
    if not days:
        return {}
    names = load_names()
    day_idx = pd.DatetimeIndex(days)
    out: dict[pd.Timestamp, int] = {}
    prev: frozenset[str] | None = None
    for m in month_starts(start, end):
        pos = day_idx.searchsorted(m, side="left") - 1  # m 보다 엄격히 이전인 마지막 거래일
        if pos < 0:
            continue
        cap = marketcap.load(day_idx[pos])["market_cap"]
        codes = select_members(cap, names, prev, top_n=top_n, buffer_n=buffer_n)
        save_snapshot(m, codes)
        prev = frozenset(codes)
        out[m] = len(codes)
    return out


def load_membership() -> dict[pd.Timestamp, frozenset[str]]:
    out: dict[pd.Timestamp, frozenset[str]] = {}
    if not config.MEMBERSHIP_DIR.exists():
        return out
    for p in sorted(config.MEMBERSHIP_DIR.glob("*.csv")):
        df = pd.read_csv(p, dtype={"code": str})
        if len(df):
            out[pd.Timestamp(p.stem)] = frozenset(df["code"].str.zfill(6))
    return out


def members_asof(when, membership: dict | None = None) -> frozenset[str]:
    """when 시점에 유니버스에 있던 종목. when 이후 스냅샷은 절대 보지 않는다."""
    m = load_membership() if membership is None else membership
    ts = pd.Timestamp(when)
    past = [d for d in m if d <= ts]
    return m[max(past)] if past else frozenset()


def all_members(membership: dict | None = None) -> list[str]:
    """한 번이라도 편입됐던 모든 종목(상폐·합병 포함)."""
    m = load_membership() if membership is None else membership
    s: set[str] = set()
    for v in m.values():
        s |= v
    return sorted(s)


def current_members(membership: dict | None = None) -> frozenset[str]:
    m = load_membership() if membership is None else membership
    return m[max(m)] if m else frozenset()


def survivorship_safe(membership: dict | None = None) -> bool:
    m = load_membership() if membership is None else membership
    return len(m) > 0


# ---------------- 종목명 (KRX Open API 스냅샷의 ISU_NM) ----------------
def load_names() -> dict[str, str]:
    if not config.NAMES_CSV.exists():
        return {}
    df = pd.read_csv(config.NAMES_CSV, dtype={"code": str, "name": str}, encoding="utf-8-sig")
    return dict(zip(df["code"].str.zfill(6), df["name"]))


def save_names(new: dict[str, str]) -> dict[str, str]:
    """기존 캐시와 병합. 이름==코드(조회 실패값)는 기존 이름을 덮지 않는다."""
    merged = load_names()
    for code, name in new.items():
        code = str(code).zfill(6)
        if not name or str(name) == code or str(name).lower() in ("nan", "none"):
            merged.setdefault(code, code)
            continue
        merged[code] = str(name)
    config.NAMES_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sorted(merged.items()), columns=["code", "name"]).to_csv(
        config.NAMES_CSV, index=False, encoding="utf-8-sig"
    )
    return merged
