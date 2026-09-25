"""투자자별 순매수 수급 수집 — KIS 종목별 투자자매매동향(일별) 하나로 과거와 매일을 모두 받는다.

  python scripts/collect_flows.py --check
  python scripts/collect_flows.py --backfill --years 10               # 유니버스 편입 이력 전 종목(상폐 포함)
  python scripts/collect_flows.py --backfill --years 10 --only-missing
  python scripts/collect_flows.py --update                            # 현재 유니버스, 마지막일-5일부터
  python scripts/collect_flows.py --status

호출량: 종목당 10년 약 85회(30거래일씩). 실전 초당 20건 제한 → 350종목 약 30분.
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli, config
from src.data import flows
from src.universe import membership


def do_backfill(log, years: int, only_missing: bool, codes: list[str] | None) -> int:
    codes = codes or membership.all_members()
    if len(codes) == 0:
        log.error("유니버스 스냅샷이 없다. collect_universe.py --backfill 먼저.")
        return 1
    start = pd.Timestamp.today().normalize() - pd.DateOffset(years=years)
    if only_missing:
        codes = [c for c in codes if not flows.flow_path(c).exists()]
    log.info("수급 백필(KIS) %d종목, 시작 %s", len(codes), f"{start:%Y-%m-%d}")
    fails = []
    for i, c in enumerate(codes, 1):
        try:
            df = flows.save(c, flows.fetch(c, start), overwrite=True)
            if i % 20 == 0 or i == len(codes):
                log.info("[%d/%d] %s %d행 (%s~%s)", i, len(codes), c, len(df),
                         f"{df.index.min():%Y-%m-%d}", f"{df.index.max():%Y-%m-%d}")
        except Exception as e:  # noqa: BLE001
            fails.append((c, str(e)))
            log.warning("[%d/%d] %s: %s", i, len(codes), c, e)
    pd.DataFrame(fails, columns=["code", "error"]).to_csv(
        config.LOGS / "flows_failures.csv", index=False, encoding="utf-8-sig")
    log.info("완료: 성공 %d, 실패 %d", len(codes) - len(fails), len(fails))
    return 0 if not fails else 2


def do_update(log, years: int, codes: list[str] | None) -> int:
    codes = codes or sorted(membership.current_members())
    if len(codes) == 0:
        log.error("유니버스 스냅샷이 없다. collect_universe.py --backfill 먼저.")
        return 1
    default_start = pd.Timestamp.today().normalize() - pd.DateOffset(years=years)
    fails, added = [], 0
    for i, c in enumerate(codes, 1):
        try:
            before = len(flows.load(c))
            df = flows.save(c, flows.fetch(c, flows.update_start(c, default_start)))
            added += len(df) - before
        except Exception as e:  # noqa: BLE001
            fails.append((c, str(e)))
            log.warning("%s: %s", c, e)
        if i % 50 == 0 or i == len(codes):
            log.info("[%d/%d] 수급 증분, 새 행 %d", i, len(codes), added)
    log.info("수급 증분 %d종목, 새 행 %d, 실패 %d", len(codes) - len(fails), added, len(fails))
    return 0 if not fails else 2


def do_status(log) -> int:
    codes = membership.all_members()
    have = [c for c in codes if flows.flow_path(c).exists()]
    print("=" * 60)
    print(f"수급 파일: {len(have)}/{len(codes)} (유니버스 편입 이력 종목 기준)")
    missing = sorted(set(codes) - set(have))
    if missing:
        print("  없음:", " ".join(missing[:30]) + (" ..." if len(missing) > 30 else ""))
    first, last, rows = [], [], 0
    for c in have:
        df = flows.load(c)
        if len(df):
            first.append(df.index.min())
            last.append(df.index.max())
            rows += len(df)
    if first:
        print(f"기간: {min(first):%Y-%m-%d} ~ {max(last):%Y-%m-%d}, 총 {rows:,}행")
        stale = sum(1 for d in last if (max(last) - d).days > 20)
        print(f"마지막일이 20일 넘게 뒤처진 파일: {stale}개 (상폐·합병이면 정상)")
    print("출처: KIS FHPTJ04160001. 피처 가중치 0 (IC 측정 전). lag=1, 분모=20일 거래대금 중앙값 shift(1)")
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--codes", nargs="*")
    a = ap.parse_args(argv)
    log = cli.setup("collect_flows")

    rc = 0
    if a.check:
        rc |= 0 if cli.kis_check(log) else 1
    if a.backfill:
        rc |= do_backfill(log, a.years, a.only_missing, a.codes)
    if a.update:
        rc |= do_update(log, a.years, a.codes)
    if a.status:
        rc |= do_status(log)
    if not any([a.check, a.backfill, a.update, a.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
