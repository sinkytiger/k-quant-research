"""공매도·신용잔고 수집 (KIS 공식 API, 현재 유니버스 = 코스피 시총 상위 200).

  python scripts/collect_short_credit.py --backfill --days 400   # 처음 (종목당 약 12 호출)
  python scripts/collect_short_credit.py --update                 # 매일: 마지막 저장일 7일 전부터
  python scripts/collect_short_credit.py --status
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.data import short_credit as sc
from src.universe import membership


def run(log, codes: list[str], start_of, workers: int = 2) -> int:
    fails = []

    def one(c):
        n = {}
        for kind, fn in (("short", sc.fetch_short), ("credit", sc.fetch_credit)):
            df = fn(c, start_of(kind, c))
            if len(df):
                sc.save(kind, c, df)
            n[kind] = len(df)
        return n

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c for c in codes}
        for i, f in enumerate(as_completed(futs), 1):
            c = futs[f]
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                fails.append(c)
                log.warning("%s: %s", c, str(e)[:200])
            if i % 50 == 0 or i == len(codes):
                log.info("[%d/%d] 공매도·신용 (실패 %d)", i, len(codes), len(fails))
    return 0 if not fails else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    log = cli.setup("collect_short_credit")
    codes = sorted(membership.current_members())
    today = pd.Timestamp.today().normalize()
    rc = 0
    if a.backfill:
        rc |= run(log, codes, lambda kind, c: today - pd.Timedelta(days=a.days), a.workers)
    if a.update:
        rc |= run(log, codes, lambda kind, c: sc.update_start(kind, c, today - pd.Timedelta(days=a.days)), a.workers)
    if a.status:
        for kind in ("short", "credit"):
            last = [sc.load(kind, c).index.max() for c in codes if sc.path(kind, c).exists()]
            print(f"{kind}: {len(last)}종목, 마지막 {max(last):%Y-%m-%d}" if last else f"{kind}: 없음")
    if not (a.backfill or a.update or a.status):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
