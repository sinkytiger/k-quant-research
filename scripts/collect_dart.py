"""DART 공시 목록 수집 (유가증권, 월 단위).

  python scripts/collect_dart.py --backfill --start 2015-09     # 10년, 약 5,000 호출
  python scripts/collect_dart.py --update                       # 이번 달 + 지난달 다시
  python scripts/collect_dart.py --status
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.data import dart


def months(start: str, end: pd.Timestamp) -> list[str]:
    return [f"{d:%Y%m}" for d in pd.period_range(pd.Timestamp(start), end, freq="M").to_timestamp()]


def collect(log, yms: list[str], force: bool) -> int:
    fails = []
    for i, ym in enumerate(yms, 1):
        if not force and dart.month_path(ym).exists():
            continue
        try:
            df = dart.fetch_month(ym)
            dart.save_month(ym, df)
            log.info("[%d/%d] %s %d건", i, len(yms), ym, len(df))
        except dart.DartError as e:
            fails.append(ym)
            log.error("%s: %s", ym, e)
            if "한도" in str(e):
                log.error("하루 한도 — 내일 같은 명령으로 이어서 받는다.")
                return 2
    return 0 if not fails else 2


def do_status() -> int:
    df = dart.load_all()
    print("=" * 60)
    if df.empty:
        print("DART 공시: 없음")
    else:
        df["cat"] = df["report_nm"].map(dart.classify)
        print(f"DART 공시(유가): {len(df):,}건, {df['rcept_dt'].min():%Y-%m-%d} ~ {df['rcept_dt'].max():%Y-%m-%d}")
        print("분류별 건수:")
        print(df["cat"].value_counts().to_string())
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--start", default="2015-09")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("collect_dart")
    today = pd.Timestamp.today().normalize()
    rc = 0
    if a.backfill:
        rc |= collect(log, months(a.start, today), a.force)
    if a.update:
        rc |= collect(log, months(f"{today - pd.DateOffset(months=1):%Y-%m}", today), force=True)
    if a.status:
        rc |= do_status()
    if not (a.backfill or a.update or a.status):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
