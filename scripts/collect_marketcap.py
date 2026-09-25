"""시가총액 스냅샷 수집 (날짜당 1회 호출로 전 종목).

  python scripts/collect_marketcap.py --check
  python scripts/collect_marketcap.py --backfill --years 10            # 매 영업일
  python scripts/collect_marketcap.py --backfill --years 10 --freq M   # 월말만(빠름)
  python scripts/collect_marketcap.py --update                          # 마지막 이후 + 0 파일 재수집
  python scripts/collect_marketcap.py --status

휴장일을 넣으면 KRX 가 0 뿐인 표를 준다 → 직전 영업일로 최대 5일 재시도.
파일명은 실제 거래일이다.
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.universe import marketcap, prices


def target_days(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> list[pd.Timestamp]:
    """KOSPI 벤치 파일이 있으면 그 거래일 달력을, 없으면 평일 달력을 쓴다."""
    bench = prices.load_bench("KOSPI")
    if len(bench):
        days = bench.index[(bench.index >= start) & (bench.index <= end)]
        # 벤치 파일 이후 기간은 평일로 채운다
        tail_start = (bench.index.max() + pd.Timedelta(days=1)) if len(bench) else start
        days = days.union(pd.bdate_range(max(start, tail_start), end))
    else:
        days = pd.bdate_range(start, end)
    s = pd.Series(days, index=days)
    if freq == "W":
        days = s.groupby(days.to_period("W")).max()
    elif freq == "M":
        days = s.groupby(days.to_period("M")).max()
    return [pd.Timestamp(d) for d in days]


def collect(log, days: list[pd.Timestamp], market: str) -> int:
    have = set(marketcap.saved_days(valid_only=True))
    todo = [d for d in days if d not in have]
    log.info("시가총액 대상 %d일, 이미 있음 %d, 받을 것 %d", len(days), len(days) - len(todo), len(todo))
    fails, holidays = [], 0
    for i, d in enumerate(todo, 1):
        try:
            actual, df = marketcap.fetch_cap(d, market=market)
        except Exception as e:  # noqa: BLE001
            fails.append((d, str(e)))
            log.warning("%s", e)
            continue
        if actual != d:
            holidays += 1
        if actual not in have:
            marketcap.save(actual, df)
            have.add(actual)
        if i % 50 == 0 or i == len(todo):
            log.info("[%d/%d] %s → %s %d종목", i, len(todo), f"{d:%Y-%m-%d}", f"{actual:%Y-%m-%d}", len(df))
    log.info("완료: 저장 %d일, 휴장일 대체 %d, 실패 %d", len(marketcap.saved_days()), holidays, len(fails))
    return 0 if not fails else 2


def do_status() -> int:
    days = marketcap.saved_days(valid_only=True)
    bad = marketcap.invalid_files()
    print("=" * 60)
    if days:
        print(f"시가총액 스냅샷: {len(days)}일 ({min(days):%Y-%m-%d} ~ {max(days):%Y-%m-%d})")
        last = marketcap.load(max(days))
        print(f"마지막 스냅샷 종목 수: {len(last)}, 시총>0: {(last['market_cap'] > 0).sum()}")
    else:
        print("시가총액 스냅샷: 없음")
    print(f"0 뿐인(무효) 파일: {len(bad)}개" + (" → --update 로 다시 받는다" if bad else ""))
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--freq", choices=["D", "W", "M"], default="D")
    ap.add_argument("--market", default="KOSPI", help="KOSPI / KOSDAQ / ALL")
    a = ap.parse_args(argv)
    log = cli.setup("collect_marketcap")
    today = pd.Timestamp.today().normalize()

    rc = 0
    if a.check:
        try:
            actual, df = marketcap.fetch_cap(today, market=a.market)
            log.info("[OK] %s 시가총액 %d종목 (오늘 요청 → %s)", a.market, len(df), f"{actual:%Y-%m-%d}")
        except Exception as e:  # noqa: BLE001
            log.error("[FAIL] %s", e)
            rc |= 1
    if a.backfill:
        start = today - pd.DateOffset(years=a.years)
        rc |= collect(log, target_days(start, today, a.freq), a.market)
    if a.update:
        bad = marketcap.invalid_files()
        for p in bad:
            log.info("0 뿐인 파일 삭제 후 재수집: %s", p.name)
            p.unlink()
        days = [pd.Timestamp(p.stem) for p in bad]
        saved = marketcap.saved_days()
        start = (max(saved) + pd.Timedelta(days=1)) if saved else today - pd.DateOffset(years=a.years)
        days += target_days(start, today, "D")
        rc |= collect(log, sorted(set(days)), a.market)
    if a.status:
        rc |= do_status()
    if not any([a.check, a.backfill, a.update, a.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
