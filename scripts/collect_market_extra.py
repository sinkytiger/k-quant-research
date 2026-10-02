"""홈 화면용 시장 데이터: 해외 지수·환율(나스닥, S&P500, VIX, 원/달러), 시장별 투자자(코스피·코스닥).

  python scripts/collect_market_extra.py     # 처음엔 약 400일, 이후 증분
"""
from __future__ import annotations

import sys

import _boot  # noqa: F401

from src import cli
from src.data import market_extra


def main(argv=None) -> int:
    log = cli.setup("collect_market_extra")
    return market_extra.update(log)


if __name__ == "__main__":
    sys.exit(main())
