"""종목 뉴스 제목 수집 (KIS). 지금 안 쌓으면 잃는 데이터라 매일 돈다.

  python scripts/collect_news.py --backfill                 # 유니버스 편입 종목, 2025-10-01 부터 (촘촘한 구간)
  python scripts/collect_news.py --update                   # 현재 유니버스, 마지막 기사 -6시간부터
  python scripts/collect_news.py --status
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import _boot  # noqa: F401
import pandas as pd

from src import cli, config
from src.data import news
from src.universe import membership


def backfill_codes(since) -> list[str]:
    """since 이후 한 번이라도 유니버스에 있던 종목 (시점별)."""
    m = membership.load_membership()
    snaps = [d for d in m if d >= pd.Timestamp(since) - pd.DateOffset(months=1)]
    out: set[str] = set()
    for d in snaps:
        out |= m[d]
    return sorted(out)


def run(log, codes: list[str], since_fn, workers: int, label: str) -> int:
    fails, added = [], 0

    def one(c):
        before = len(news.load(c))
        df = news.save(c, news.fetch(c, since_fn(c)))
        return len(df) - before

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c for c in codes}
        for i, f in enumerate(as_completed(futs), 1):
            c = futs[f]
            try:
                added += f.result()
            except Exception as e:  # noqa: BLE001
                fails.append((c, str(e)))
                log.warning("%s: %s", c, e)
            if i % 20 == 0 or i == len(codes):
                log.info("[%d/%d] %s 새 기사 %d", i, len(codes), label, added)
    pd.DataFrame(fails, columns=["code", "error"]).to_csv(
        config.LOGS / f"news_{label}_failures.csv", index=False, encoding="utf-8-sig")
    log.info("뉴스 %s %d종목, 새 기사 %d, 실패 %d", label, len(codes) - len(fails), added, len(fails))
    return 0 if not fails else 2


def do_status() -> int:
    files = sorted(news.news_dir().glob("*.csv")) if news.news_dir().exists() else []
    n, first, last, per_day = 0, [], [], []
    for p in files:
        df = news.load(p.stem)
        if len(df):
            n += len(df)
            first.append(df["dt"].min())
            last.append(df["dt"].max())
            per_day.append(len(df) / max(1, (df["dt"].max() - df["dt"].min()).days))
    print("=" * 60)
    print(f"뉴스 파일: {len(files)}종목, 기사 {n:,}건 (종목 간 중복 포함)")
    if first:
        print(f"기간: {min(first):%Y-%m-%d} ~ {max(last):%Y-%m-%d %H:%M}")
        s = pd.Series(per_day)
        print(f"종목당 하루 기사 수: 중앙값 {s.median():.1f}, 상위 10% {s.quantile(0.9):.1f}")
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--since", default=news.DENSE_SINCE)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--codes", nargs="*")
    a = ap.parse_args(argv)
    log = cli.setup("collect_news")
    rc = 0
    if a.backfill:
        codes = a.codes or backfill_codes(a.since)
        if a.only_missing:
            codes = [c for c in codes if not news.news_path(c).exists()]
        log.info("뉴스 백필 %d종목, %s 부터", len(codes), a.since)
        rc |= run(log, codes, lambda c: a.since, a.workers, "backfill")
    if a.update:
        codes = a.codes or sorted(membership.current_members())
        rc |= run(log, codes, lambda c: news.update_since(c, a.since), a.workers, "update")
    if a.status:
        rc |= do_status()
    if not (a.backfill or a.update or a.status):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
