"""유니버스 수집: KRX Open API 일별 스냅샷 → 종목별 수정주가·시가총액·벤치마크,
pykrx → KOSPI200 시점별 구성종목.

순서(기획서 4장, API 전환 반영):
  python scripts/collect_universe.py --check
  python scripts/collect_universe.py --membership --start 2016-01-01   # pykrx, KRX_ID/PW 필요
  python scripts/collect_universe.py --backfill --period 10y           # KRX Open API (재실행 안전, 한도 넘으면 다음날 이어서)
  python scripts/collect_universe.py --status
일일 증분: --update
코스닥까지: --markets stk ksq

KRX Open API 스냅샷에는 그날 상장된 전 종목이 들어 있어 상폐·합병 종목 시세가 자동으로 포함된다.
그래서 예전의 yfinance → KRX 2단계와 --only-missing --krx-only 재시도는 필요 없다(옵션은 받아만 둔다).
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli, config, krx_api
from src.universe import krx_daily, kospi200, marketcap, prices


def do_membership(log, start: str, end: str | None, force: bool) -> int:
    fails = []
    months = kospi200.month_starts(start, end)
    for i, d in enumerate(months, 1):
        if not force and kospi200.snapshot_path(d).exists():
            continue
        err = "빈 응답"
        try:
            codes = kospi200.fetch_members(d)
        except Exception as e:  # noqa: BLE001
            codes, err = [], repr(e)
        if len(codes) == 0:
            fails.append(d)
            log.error("스냅샷 실패 %s: %s", f"{d:%Y-%m-%d}", err)
            if len(fails) >= 3 and len(kospi200.load_membership()) == 0:
                log.error("연속 실패 — KRX 로그인(.env KRX_ID/KRX_PW)을 확인하고 다시 실행")
                return 1
            continue
        kospi200.save_snapshot(d, codes)
        log.info("[%d/%d] %s %d종목", i, len(months), f"{d:%Y-%m-%d}", len(codes))
    m = kospi200.load_membership()
    log.info("스냅샷 %d개, 누적 종목 %d개, 실패 %d개", len(m), len(kospi200.all_members(m)), len(fails))
    if len(m) == 0:
        log.error("survivorship_safe=False — 구성종목을 하나도 받지 못함. 티커를 지어내지 말 것.")
        return 1
    return 0 if not fails else 2


def collect_snapshots(log, datasets: list[str], start, end) -> int:
    """평일마다 datasets 스냅샷. 이미 있는 날·휴장일은 건너뛴다. 한도에 걸리면 멈추고 2 반환."""
    days = krx_daily.candidate_days(start, end)
    for ds in datasets:
        have = set(krx_daily.saved_days(ds))
        todo = [d for d in days if d not in have]
        log.info("[%s] 대상 %d일, 있음 %d, 받을 것 %d (오늘 사용 %d건)",
                 ds, len(days), len(days) - len(todo), len(todo), krx_api.used_today())
        stats = {"saved": 0, "holiday": 0, "exists": 0}
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


def rebuild(log, markets: list[str]) -> None:
    for ds in markets:
        raw = krx_daily.load_snapshots(ds)
        tidy = krx_daily.tidy_stock(raw)
        if tidy.empty:
            log.warning("[%s] 스냅샷 없음", ds)
            continue
        built = krx_daily.build_prices(tidy)
        log.info("[%s] 종목별 수정주가 %d종목 저장 (%s~%s)", ds, len(built),
                 f"{tidy['Date'].min():%Y-%m-%d}", f"{tidy['Date'].max():%Y-%m-%d}")
        if ds == "stk":
            n = krx_daily.build_marketcap(tidy)
            log.info("[stk] 시가총액 스냅샷 %d일", n)
        kospi200.save_names(krx_daily.names_from(tidy))
    b = krx_daily.build_bench()
    log.info("벤치마크: %s", b)


def do_backfill(log, period: str, markets: list[str]) -> int:
    start = cli.parse_period(period)
    end = pd.Timestamp.today().normalize()
    rc = collect_snapshots(log, markets + ["idx_kospi", "etf"], start, end)
    rebuild(log, markets)
    if rc == 2:
        log.info("KRX API 하루 한도 보호로 멈췄다. 내일 같은 명령을 다시 실행하면 이어서 받는다.")
    return rc


def do_update(log, markets: list[str]) -> int:
    last = krx_daily.saved_days("stk")
    start = (last[-1] + pd.Timedelta(days=1)) if last else cli.parse_period("10y")
    end = pd.Timestamp.today().normalize()
    rc = collect_snapshots(log, markets + ["idx_kospi", "etf"], start, end)
    rebuild(log, markets)
    return rc


def do_status(log) -> int:
    m = kospi200.load_membership()
    members = kospi200.all_members(m)
    print("=" * 60)
    print(f"데이터 폴더: {config.DATA}")
    for ds in krx_daily.DATASETS:
        days = krx_daily.saved_days(ds)
        if days:
            print(f"KRX 스냅샷 {ds}: {len(days)}일 ({days[0]:%Y-%m-%d} ~ {days[-1]:%Y-%m-%d})")
    print(f"확인된 휴장일: {len(krx_daily.load_holidays())}일, 오늘 API 사용 {krx_api.used_today()}건")
    if m:
        print(f"KOSPI200 구성종목 스냅샷: {len(m)}개 ({min(m):%Y-%m-%d} ~ {max(m):%Y-%m-%d}), 누적 종목 {len(members)}개")
    else:
        print("KOSPI200 구성종목 스냅샷: 없음 (pykrx 로그인 필요) → survivorship_safe=False")
    files = sorted(p.stem for p in config.PRICES_DIR.glob("*.csv")) if config.PRICES_DIR.exists() else []
    print(f"종목별 일봉 파일: {len(files)}개")
    if members:
        missing = [c for c in members if c not in set(files)]
        print(f"  KOSPI200 편입 이력 종목 중 일봉 없음: {len(missing)}개" +
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
    print(f"종목명 캐시: {len(kospi200.load_names())}개")
    safe = bool(m) and bool(members) and all(c in set(files) for c in members)
    print(f"survivorship_safe={safe}")
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="KRX Open API·KIS·pykrx 접근 점검")
    ap.add_argument("--membership", action="store_true", help="월초 KOSPI200 구성종목 (pykrx)")
    ap.add_argument("--backfill", action="store_true", help="KRX 스냅샷 백필 + 재구성")
    ap.add_argument("--update", action="store_true", help="마지막 스냅샷 이후 증분 + 재구성")
    ap.add_argument("--bench", action="store_true", help="벤치마크만 재구성")
    ap.add_argument("--rebuild", action="store_true", help="API 호출 없이 스냅샷으로 재구성만")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--period", default="10y")
    ap.add_argument("--markets", nargs="+", default=["stk"], choices=["stk", "ksq"])
    ap.add_argument("--force", action="store_true", help="이미 있는 구성종목 스냅샷도 다시 받기")
    ap.add_argument("--only-missing", action="store_true", help="(호환용, 항상 적용)")
    ap.add_argument("--krx-only", action="store_true", help="(호환용, 항상 KRX)")
    a = ap.parse_args(argv)
    log = cli.setup("collect_universe")

    rc = 0
    if a.check:
        rc |= 0 if cli.check_all(log) else 1
    if a.membership:
        rc |= do_membership(log, a.start, a.end, a.force)
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
