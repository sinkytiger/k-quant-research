"""시가총액 스냅샷. KRX Open API 유가증권 일별매매(MKTCAP, LIST_SHRS)에서 만든다.

  python scripts/collect_marketcap.py --backfill --years 10   # 스냅샷이 없으면 받고, 시총 파일로 변환
  python scripts/collect_marketcap.py --update
  python scripts/collect_marketcap.py --status

KRX Open API 는 휴장일에 빈 배열을 준다(예전 pykrx 처럼 0 뿐인 표가 아니다).
0 뿐인 파일이 남아 있으면 무효로 보고 --update 에서 지운 뒤 다시 만든다.
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.universe import krx_daily, marketcap


def build(log, start=None) -> None:
    tidy = krx_daily.tidy_stock(krx_daily.load_snapshots("stk", start=start))
    n = krx_daily.build_marketcap(tidy)
    log.info("시가총액 파일 %d일 작성", n)


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
    print(f"0 뿐인(무효) 파일: {len(bad)}개" + (" → --update 로 다시 만든다" if bad else ""))
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--years", type=int, default=10)
    a = ap.parse_args(argv)
    log = cli.setup("collect_marketcap")
    today = pd.Timestamp.today().normalize()

    rc = 0
    if a.backfill:
        from collect_universe import collect_snapshots

        start = today - pd.DateOffset(years=a.years)
        rc |= collect_snapshots(log, ["stk"], start, today)
        build(log)
    if a.update:
        for p in marketcap.invalid_files():
            log.info("0 뿐인 파일 삭제: %s", p.name)
            p.unlink()
        from collect_universe import collect_snapshots

        saved = krx_daily.saved_days("stk")
        start = (saved[-1] + pd.Timedelta(days=1)) if saved else today - pd.DateOffset(years=a.years)
        rc |= collect_snapshots(log, ["stk"], start, today)
        build(log)
    if a.status:
        rc |= do_status()
    if not any([a.backfill, a.update, a.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
