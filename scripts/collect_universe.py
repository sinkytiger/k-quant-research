"""KOSPI200 유니버스 수집: 구성종목 스냅샷, 종목명, 일봉, 벤치마크.

순서(기획서 4장):
  python scripts/collect_universe.py --check
  python scripts/collect_universe.py --membership --start 2016-01-01
  python scripts/collect_universe.py --backfill --period 10y
  python scripts/collect_universe.py --backfill --only-missing --krx-only
  python scripts/collect_universe.py --bench --period 10y
  python scripts/collect_universe.py --status
일일 증분: --update
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli, config
from src.universe import kospi200, prices


def do_membership(log, start: str, end: str | None, force: bool) -> int:
    fails = []
    months = kospi200.month_starts(start, end)
    for i, d in enumerate(months, 1):
        if not force and kospi200.snapshot_path(d).exists():
            continue
        try:
            codes = kospi200.fetch_members(d)
        except Exception as e:  # noqa: BLE001
            codes, err = [], str(e)
        else:
            err = "빈 응답"
        if len(codes) == 0:
            fails.append(d)
            log.error("스냅샷 실패 %s: %s", f"{d:%Y-%m-%d}", err)
            continue
        kospi200.save_snapshot(d, codes)
        log.info("[%d/%d] %s %d종목", i, len(months), f"{d:%Y-%m-%d}", len(codes))
    m = kospi200.load_membership()
    log.info("스냅샷 %d개, 누적 종목 %d개, 실패 %d개", len(m), len(kospi200.all_members(m)), len(fails))
    if len(m) == 0:
        log.error("survivorship_safe=False — 구성종목을 하나도 받지 못함. 티커를 지어내지 말 것.")
        return 1
    do_names(log)
    return 0 if not fails else 2


def do_names(log) -> None:
    codes = kospi200.all_members()
    if len(codes) == 0:
        return
    known = kospi200.load_names()
    todo = [c for c in codes if known.get(c, c) == c]
    if not todo:
        log.info("종목명 캐시 최신 (%d개)", len(known))
        return
    merged = kospi200.save_names(kospi200.fetch_names(todo))
    unnamed = sum(1 for c in codes if merged.get(c, c) == c)
    log.info("종목명 %d개 저장, 이름 못 찾음 %d개(상폐 종목일 수 있음)", len(merged), unnamed)


def do_backfill(log, period: str, only_missing: bool, krx_only: bool, codes: list[str] | None) -> int:
    codes = codes or kospi200.all_members()
    if len(codes) == 0:
        log.error("구성종목 스냅샷이 없다. --membership 먼저. (survivorship_safe=False)")
        return 1
    start = cli.parse_period(period).strftime("%Y-%m-%d")
    if only_missing:
        codes = [c for c in codes if not prices.price_path(c).exists()]
    log.info("일봉 백필 %d종목, 시작 %s, krx_only=%s", len(codes), start, krx_only)
    fails, src_count = [], {"yf": 0, "krx": 0}
    for i, c in enumerate(codes, 1):
        try:
            df, src = prices.backfill(c, start, krx_only=krx_only)
            src_count[src] += 1
            if i % 20 == 0 or i == len(codes):
                log.info("[%d/%d] %s %s %d행 (%s~%s)", i, len(codes), c, src, len(df),
                         f"{df.index.min():%Y-%m-%d}", f"{df.index.max():%Y-%m-%d}")
        except Exception as e:  # noqa: BLE001
            fails.append((c, str(e)))
            log.warning("[%d/%d] %s", i, len(codes), e)
    _write_failures("prices_failures.csv", fails)
    log.info("완료: yf %d, krx %d, 실패 %d", src_count["yf"], src_count["krx"], len(fails))
    if fails and not krx_only:
        log.info("실패 종목은 --backfill --only-missing --krx-only 로 다시 시도")
    return 0 if not fails else 2


def do_update(log, period: str) -> int:
    m = kospi200.load_membership()
    codes = kospi200.all_members(m)
    current = kospi200.current_members(m)
    default_start = cli.parse_period(period).strftime("%Y-%m-%d")
    today = pd.Timestamp.today().normalize()
    fails, skipped = [], 0
    for i, c in enumerate(codes, 1):
        old = prices.load(c)
        # 지수에서 빠졌고 60일 넘게 거래가 없는 종목(상폐·합병)은 더 받을 게 없다
        if len(old) and c not in current and (today - old.index.max()).days > 60:
            skipped += 1
            continue
        try:
            prices.update(c, default_start)
        except Exception as e:  # noqa: BLE001
            fails.append((c, str(e)))
            log.warning("%s", e)
    _write_failures("prices_update_failures.csv", fails)
    log.info("증분 갱신 %d종목, 건너뜀(상폐 추정) %d, 실패 %d", len(codes) - skipped, skipped, len(fails))
    rc = do_bench(log, "1y", merge=True)
    return 2 if fails or rc else 0


def do_bench(log, period: str, merge: bool = False) -> int:
    start = cli.parse_period(period).strftime("%Y-%m-%d")
    rc = 0
    for name in prices.BENCH:
        try:
            df, src = prices.fetch_bench(name, start)
            if merge:
                old = prices.load_bench(name)
                if len(old):
                    df = pd.concat([old.drop(columns="source", errors="ignore"), df])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
            df = prices.save_bench(name, df, src)
            log.info("벤치 %s: %d행 %s~%s (%s)", name, len(df),
                     f"{df.index.min():%Y-%m-%d}", f"{df.index.max():%Y-%m-%d}", src)
        except Exception as e:  # noqa: BLE001
            log.error("%s", e)
            rc = 2
    return rc


def do_status(log) -> int:
    m = kospi200.load_membership()
    codes = kospi200.all_members(m)
    safe = kospi200.survivorship_safe(m)
    print("=" * 60)
    print(f"데이터 폴더: {config.DATA}")
    if m:
        print(f"구성종목 스냅샷: {len(m)}개 ({min(m):%Y-%m-%d} ~ {max(m):%Y-%m-%d}), 누적 종목 {len(codes)}개")
    else:
        print("구성종목 스냅샷: 없음")
    have = [c for c in codes if prices.price_path(c).exists()]
    missing = [c for c in codes if c not in set(have)]
    print(f"일봉 파일: {len(have)}/{len(codes)}  (없음 {len(missing)}개)")
    if missing:
        print("  없음:", " ".join(missing[:30]) + (" ..." if len(missing) > 30 else ""))
    srcs = {"yf": 0, "krx": 0}
    first, last = [], []
    for c in have:
        df = prices.load(c)
        if len(df):
            s = str(df["source"].iloc[-1]) if "source" in df.columns else "?"
            srcs[s] = srcs.get(s, 0) + 1
            first.append(df.index.min())
            last.append(df.index.max())
    print(f"출처: {srcs}")
    if first:
        print(f"기간: 가장 이른 시작 {min(first):%Y-%m-%d}, 가장 늦은 끝 {max(last):%Y-%m-%d}")
    dl = prices.delisted_guess(have)
    print(f"상장폐지/합병 추정: {len(dl)}개")
    span_years = (max(m) - min(m)).days / 365.25 if m else 0
    if span_years >= 5 and len(dl) == 0:
        print("  [경고] 10년 구간에서 상폐/합병 추정이 0이면 정상이 아니다. 생존편향이 남아 있다.")
    for c, d in dl[:15]:
        print(f"  {c} 마지막 {d}")
    for name in prices.BENCH:
        df = prices.load_bench(name)
        if len(df):
            print(f"벤치 {name}: {df.index.min():%Y-%m-%d} ~ {df.index.max():%Y-%m-%d} ({len(df)}행)")
        else:
            print(f"벤치 {name}: 없음")
    names = kospi200.load_names()
    print(f"종목명 캐시: {len(names)}개")
    safe = safe and len(missing) == 0
    print(f"survivorship_safe={safe}")
    print("=" * 60)
    return 0


def _write_failures(fname: str, fails: list[tuple[str, str]]) -> None:
    p = config.LOGS / fname
    pd.DataFrame(fails, columns=["code", "error"]).to_csv(p, index=False, encoding="utf-8-sig")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="KRX 접근 점검")
    ap.add_argument("--membership", action="store_true", help="월초 구성종목 스냅샷 수집")
    ap.add_argument("--names", action="store_true", help="종목명 캐시 갱신")
    ap.add_argument("--backfill", action="store_true", help="일봉 전체 수집")
    ap.add_argument("--update", action="store_true", help="일봉 증분 갱신 + 벤치")
    ap.add_argument("--bench", action="store_true", help="벤치마크(KODEX200, KOSPI) 수집")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--period", default="10y")
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--krx-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="이미 있는 스냅샷도 다시 받기")
    ap.add_argument("--codes", nargs="*", help="특정 종목만")
    a = ap.parse_args(argv)
    log = cli.setup("collect_universe")

    rc = 0
    if a.check:
        rc |= 0 if cli.krx_check(log) else 1
    if a.membership:
        rc |= do_membership(log, a.start, a.end, a.force)
    if a.names:
        do_names(log)
    if a.backfill:
        rc |= do_backfill(log, a.period, a.only_missing, a.krx_only, a.codes)
    if a.update:
        rc |= do_update(log, a.period)
    if a.bench:
        rc |= do_bench(log, a.period)
    if a.status:
        rc |= do_status(log)
    if not any([a.check, a.membership, a.names, a.backfill, a.update, a.bench, a.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
