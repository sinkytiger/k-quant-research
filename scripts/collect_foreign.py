"""외국인 지분율 수집 (KIS 공식 API, 현재 유니버스 = 코스피 시총 상위 200).

  python scripts/collect_foreign.py --backfill   # 처음: 일·주·월 소진율 + 외국인 한도 (종목당 4 호출, 약 2.5년)
  python scripts/collect_foreign.py --update     # 매일: 일별 30거래일 (종목당 1 호출, 월요일·한도 모르는 종목은 한도도 = 1 호출 더)
  python scripts/collect_foreign.py --status
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.data import foreign as fx
from src.universe import membership


def run(log, codes: list[str], freqs, workers: int = 2, limits: bool = True) -> int:
    """freqs 로 소진율을 받고, limits 면 외국인 한도도 다시 구한다."""
    if not codes:
        return 0
    fails, lim = [], {}

    def one(c):
        fx.save(c, fx.fetch(c, freqs))
        if limits:
            lim[c] = fx.fetch_limit(c)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c for c in codes}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                fails.append(futs[f])
                log.warning("%s: %s", futs[f], str(e)[:200])
            if i % 50 == 0 or i == len(codes):
                log.info("[%d/%d] 외국인 지분율 (실패 %d)", i, len(codes), len(fails))
    if lim:
        fx.save_limits(lim)
        capped = {c: v for c, v in lim.items() if v is not None and v < 100}
        log.info("외국인 한도 종목 %d개: %s", len(capped), capped)
    return 0 if not fails else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--limits", action="store_true", help="한도만 다시 구한다")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    log = cli.setup("collect_foreign")
    codes = sorted(membership.current_members())
    rc = 0
    if a.backfill:
        rc |= run(log, codes, ("D", "W", "M"), a.workers)
    if a.update:
        known = fx.load_limits()
        old = [c for c in codes if fx.path(c).exists()]
        monday = pd.Timestamp.today().dayofweek == 0
        rc |= run(log, [c for c in old if c in known], ("D",), a.workers, limits=monday)
        rc |= run(log, [c for c in old if c not in known], ("D",), a.workers, limits=True)
        rc |= run(log, [c for c in codes if c not in old], ("D", "W", "M"), a.workers)  # 새로 편입
    if a.limits:
        rc |= run(log, codes, (), a.workers)
    if a.status:
        last = [fx.load(c).index.max() for c in codes if fx.path(c).exists()]
        lim = fx.load_limits()
        print(f"외국인 지분율: {len(last)}종목, 마지막 {max(last):%Y-%m-%d}, 한도 {len(lim)}종목 (100 미만 {sum(v < 100 for v in lim.values())})"
              if last else "없음")
    if not (a.backfill or a.update or a.limits or a.status):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
