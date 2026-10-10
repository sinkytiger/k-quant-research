"""대차거래(대차잔고) 수집 (KIS 공식 API): 코스피·코스닥 전체 + 현재 유니버스(코스피 시총 상위 200).

  python scripts/collect_lending.py --backfill --days 400   # 처음 (종목당 약 11 호출)
  python scripts/collect_lending.py --update                 # 매일: 마지막 저장일 7일 전부터
  python scripts/collect_lending.py --status
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.data import lending as ld
from src.universe import membership


def run(log, codes: list[str], start_of, workers: int = 2) -> int:
    fails = []

    def one(c):
        df = ld.fetch(c, start_of(c))
        if len(df):
            ld.save(c, df)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c for c in codes}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                fails.append(futs[f])
                log.warning("%s: %s", futs[f], str(e)[:200])
            if i % 50 == 0 or i == len(codes):
                log.info("[%d/%d] 대차거래 (실패 %d)", i, len(codes), len(fails))
    return 0 if not fails else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    log = cli.setup("collect_lending")
    codes = list(ld.MARKETS) + sorted(membership.current_members())
    first = pd.Timestamp.today().normalize() - pd.Timedelta(days=a.days)
    rc = 0
    if a.backfill:
        rc |= run(log, codes, lambda c: first, a.workers)
    if a.update:
        rc |= run(log, codes, lambda c: ld.update_start(c, first), a.workers)
    if a.status:
        last = [ld.load(c).index.max() for c in codes if ld.path(c).exists()]
        print(f"대차거래: {len(last)}개 (시장 2 포함), 마지막 {max(last):%Y-%m-%d}" if last else "없음")
    if not (a.backfill or a.update or a.status):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
