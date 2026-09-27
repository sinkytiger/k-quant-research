"""유니버스 수집: KRX Open API 일별 스냅샷 → 종목별 수정주가·시가총액·벤치마크·시점별 유니버스.

공식 API(KRX Open API)만 쓴다. 로그인 스크래핑(pykrx)은 쓰지 않는다.

  python scripts/collect_universe.py --check
  python scripts/collect_universe.py --backfill --period 11y     # 재실행 안전, 하루 한도 넘으면 다음날 이어서
  python scripts/collect_universe.py --membership --start 2016-01-01   # 시총 상위 200 월초 스냅샷 (API 호출 없음)
  python scripts/collect_universe.py --status
일일 증분: --update   코스닥까지: --markets stk ksq

스냅샷에는 그날 상장된 전 종목이 들어 있어 상폐·합병 종목 시세가 자동으로 포함된다.
유니버스는 실제 KOSPI200 이 아니라 "직전 거래일 시총 상위 200 보통주(버퍼 220)"다 (membership.py).
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli, config, krx_api
from src.universe import krx_daily, marketcap, membership, prices


def do_membership(log, start: str, end: str | None) -> int:
    built = membership.build(start, end)
    if not built:
        log.error("시가총액 스냅샷이 없다. --backfill 먼저. (survivorship_safe=False)")
        return 1
    m = membership.load_membership()
    log.info("유니버스 월초 스냅샷 %d개 (%s~%s), 누적 종목 %d개",
             len(built), f"{min(built):%Y-%m-%d}", f"{max(built):%Y-%m-%d}", len(membership.all_members(m)))
    return 0


def collect_snapshots(log, datasets: list[str], start, end) -> int:
    """평일마다 datasets 스냅샷. 이미 있는 날·휴장일은 건너뛴다. 한도에 걸리면 멈추고 2 반환."""
    # 휴장일 달력(stk)을 먼저 채워야 다른 데이터셋이 휴장일을 호출하지 않는다
    datasets = sorted(datasets, key=lambda d: d != krx_daily.CALENDAR_DS)
    for ds in datasets:
        days = krx_daily.candidate_days(start, end)
        if ds != krx_daily.CALENDAR_DS:  # 거래일로 확인된 날만
            cal = set(krx_daily.saved_days(krx_daily.CALENDAR_DS))
            days = [d for d in days if d in cal] if cal else days
        have = set(krx_daily.saved_days(ds))
        todo = [d for d in days if d not in have]
        log.info("[%s] 대상 %d일, 있음 %d, 받을 것 %d (오늘 사용 %d건)",
                 ds, len(days), len(days) - len(todo), len(todo), krx_api.used_today())
        stats = {"saved": 0, "holiday": 0, "missing": 0, "exists": 0}
        for i, d in enumerate(todo, 1):
            try:
                stats[krx_daily.collect_day(ds, d)] += 1
            except krx_api.QuotaExceeded as e:
                log.warning("%s", e)
                return 2
            except krx_api.KrxApiError as e:
                log.error("%s", e)
                if "401" in str(e):
                    return 1
                continue
            if i % 100 == 0 or i == len(todo):
                log.info("[%s] %d/%d %s  %s", ds, i, len(todo), f"{d:%Y-%m-%d}", stats)
    return 0


def rebuild(log, markets: list[str] | None = None) -> None:
    """스냅샷 → 종목별 수정주가·시총·종목명·벤치.

    markets 인자와 상관없이 받아 둔 시장(stk, ksq)을 **모두 합쳐** 종목별로 한 번에 만든다.
    코스닥→코스피 이전상장 종목(카카오, 셀트리온 등)은 같은 코드로 두 시장에 나타나므로,
    시장별로 따로 쓰면 뒤에 쓴 시장이 앞의 이력을 덮어쓴다.
    """
    parts = []
    for ds in krx_daily.MARKETS:
        tidy = krx_daily.tidy_stock(krx_daily.load_snapshots(ds))
        if tidy.empty:
            continue
        log.info("[%s] %d행 (%s~%s)", ds, len(tidy), f"{tidy['Date'].min():%Y-%m-%d}", f"{tidy['Date'].max():%Y-%m-%d}")
        if ds == "stk":
            log.info("[stk] 시가총액 스냅샷 %d일", krx_daily.build_marketcap(tidy))
        parts.append(tidy)
    if not parts:
        log.warning("스냅샷 없음")
        return
    tidy = pd.concat(parts, ignore_index=True)
    del parts
    built = krx_daily.build_prices(tidy)
    moved = krx_daily.transferred_codes(tidy)
    log.info("종목별 수정주가 %d종목 저장 (시장 이전 종목 %d개 이어 붙임)", len(built), len(moved))
    membership.save_names(krx_daily.names_from(tidy))
    b = krx_daily.build_bench()
    log.info("벤치마크: %s", b)


def do_backfill(log, period: str, markets: list[str]) -> int:
    start = cli.parse_period(period)
    end = pd.Timestamp.today().normalize()
    rc = collect_snapshots(log, markets + ["idx_kospi", "etf"], start, end)
    rebuild(log, markets)
    do_membership(log, "2016-01-01", None)
    if rc == 2:
        log.info("KRX API 하루 한도 보호로 멈췄다. 내일 같은 명령을 다시 실행하면 이어서 받는다.")
    return rc


def do_update(log, markets: list[str]) -> int:
    last = krx_daily.saved_days("stk")
    start = (last[-1] + pd.Timedelta(days=1)) if last else cli.parse_period("10y")
    end = pd.Timestamp.today().normalize()
    rc = collect_snapshots(log, markets + ["idx_kospi", "etf"], start, end)
    rebuild(log, markets)
    do_membership(log, "2016-01-01", None)
    return rc


def do_status(log) -> int:
    m = membership.load_membership()
    members = membership.all_members(m)
    print("=" * 60)
    print(f"데이터 폴더: {config.DATA}")
    for ds in krx_daily.DATASETS:
        days = krx_daily.saved_days(ds)
        if days:
            print(f"KRX 스냅샷 {ds}: {len(days)}일 ({days[0]:%Y-%m-%d} ~ {days[-1]:%Y-%m-%d})")
    print(f"확인된 휴장일: {len(krx_daily.load_holidays())}일, 오늘 API 사용 {krx_api.used_today()}건")
    if m:
        print(f"유니버스(시총 상위 200) 스냅샷: {len(m)}개 ({min(m):%Y-%m-%d} ~ {max(m):%Y-%m-%d}), 누적 종목 {len(members)}개")
    else:
        print("유니버스 스냅샷: 없음 (--membership) → survivorship_safe=False")
    files = sorted(p.stem for p in config.PRICES_DIR.glob("*.csv")) if config.PRICES_DIR.exists() else []
    print(f"종목별 일봉 파일: {len(files)}개")
    if members:
        missing = [c for c in members if c not in set(files)]
        print(f"  편입 이력 종목 중 일봉 없음: {len(missing)}개" +
              (f" ({' '.join(missing[:20])})" if missing else ""))
        dl = prices.delisted_guess(members)
        print(f"  편입 이력 종목 중 상장폐지/합병 추정: {len(dl)}개")
        span = (max(m) - min(m)).days / 365.25
        if span >= 5 and len(dl) == 0:
            print("  [경고] 10년 구간에서 상폐/합병 추정이 0이면 정상이 아니다. 생존편향이 남아 있다.")
    else:
        dl = prices.delisted_guess(files)
        print(f"  전 종목 중 상장폐지/합병 추정: {len(dl)}개")
    for name in krx_daily.BENCH_SOURCES:
        df = prices.load_bench(name)
        print(f"벤치 {name}: " + (f"{df.index.min():%Y-%m-%d} ~ {df.index.max():%Y-%m-%d} ({len(df)}행)" if len(df) else "없음"))
    caps = marketcap.saved_days()
    print(f"시가총액 스냅샷: {len(caps)}일" + (f" ({caps[0]:%Y-%m-%d} ~ {caps[-1]:%Y-%m-%d})" if caps else ""))
    print(f"종목명 캐시: {len(membership.load_names())}개")
    safe = bool(m) and bool(members) and all(c in set(files) for c in members)
    print(f"survivorship_safe={safe}")
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="KRX Open API·KIS 접근 점검")
    ap.add_argument("--membership", action="store_true", help="시총 상위 200 월초 유니버스 재구성 (API 호출 없음)")
    ap.add_argument("--backfill", action="store_true", help="KRX 스냅샷 백필 + 재구성")
    ap.add_argument("--update", action="store_true", help="마지막 스냅샷 이후 증분 + 재구성")
    ap.add_argument("--bench", action="store_true", help="벤치마크만 재구성")
    ap.add_argument("--rebuild", action="store_true", help="API 호출 없이 스냅샷으로 재구성만")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--period", default="10y")
    ap.add_argument("--markets", nargs="+", default=["stk"], choices=["stk", "ksq"])
    ap.add_argument("--only-missing", action="store_true", help="(호환용, 항상 적용)")
    ap.add_argument("--krx-only", action="store_true", help="(호환용, 항상 KRX)")
    a = ap.parse_args(argv)
    log = cli.setup("collect_universe")

    rc = 0
    if a.check:
        rc |= 0 if cli.check_all(log) else 1
    if a.membership:
        rc |= do_membership(log, a.start, a.end)
    if a.backfill:
        rc |= do_backfill(log, a.period, a.markets)
    if a.update:
        rc |= do_update(log, a.markets)
    if a.rebuild:
        rebuild(log, a.markets)
    elif a.bench and not (a.backfill or a.update):
        log.info("벤치마크: %s", krx_daily.build_bench())
    if a.status:
        rc |= do_status(log)
    if not any([a.check, a.membership, a.backfill, a.update, a.bench, a.rebuild, a.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
