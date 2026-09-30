"""현금배당 일정 수집 (KIS 예탁원정보). 종목당 1회 호출로 2015~ 전체.

  python scripts/collect_dividends.py            # 유니버스 편입 이력 전 종목 (월 1회면 충분)
  python scripts/collect_dividends.py --codes 005930
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401

from src import cli
from src.data import dividends
from src.universe import membership


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", nargs="*")
    a = ap.parse_args(argv)
    log = cli.setup("collect_dividends")
    codes = a.codes or membership.all_members()
    fails, n = [], 0
    for i, c in enumerate(codes, 1):
        try:
            df = dividends.fetch(c)
            dividends.save(c, df)
            n += len(df)
        except Exception as e:  # noqa: BLE001
            fails.append(c)
            log.warning("%s: %s", c, e)
        if i % 50 == 0 or i == len(codes):
            log.info("[%d/%d] 배당 %d건", i, len(codes), n)
    log.info("배당 수집 %d종목, %d건, 실패 %d", len(codes) - len(fails), n, len(fails))
    return 0 if not fails else 2


if __name__ == "__main__":
    sys.exit(main())
