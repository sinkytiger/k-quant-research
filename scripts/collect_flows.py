"""투자자별 순매수 수급 수집 (KOSPI200 누적 편입 종목 전체, 상폐 포함).

  python scripts/collect_flows.py --check                     # pykrx (KRX 로그인)
  python scripts/collect_flows.py --backfill --years 10       # 과거: pykrx
  python scripts/collect_flows.py --backfill --years 10 --only-missing
  python scripts/collect_flows.py --update                    # 매일: KIS 최근 30거래일
  python scripts/collect_flows.py --verify                    # pykrx 와 KIS 겹치는 구간 비교
  python scripts/collect_flows.py --status
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli, config
from src.data import flows
from src.universe import kospi200


def do_check(log) -> bool:
    end = pd.Timestamp.today().normalize()
    try:
        df = flows.fetch("005930", end - pd.Timedelta(days=14), end)
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] 수급 조회 실패: %s", e)
        return False
    missing = [c for c in flows.FLOW_COLS if c not in df.columns]
    if missing:
        log.error("[FAIL] 컬럼 없음: %s (받은 컬럼: %s)", missing, list(df.columns))
        return False
    log.info("[OK] 005930 수급 %d행, 마지막 %s", len(df), f"{df.index.max():%Y-%m-%d}")
    log.info("%s", df.tail(3).to_string())
    return True


def do_backfill(log, years: int, only_missing: bool, codes: list[str] | None) -> int:
    codes = codes or kospi200.all_members()
    if len(codes) == 0:
        log.error("구성종목 스냅샷이 없다. collect_universe.py --membership 먼저.")
        return 1
    start = pd.Timestamp.today().normalize() - pd.DateOffset(years=years)
    if only_missing:
        codes = [c for c in codes if not flows.flow_path(c).exists()]
    log.info("수급 백필 %d종목, 시작 %s", len(codes), f"{start:%Y-%m-%d}")
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


def update_codes(log, top_n: int = 250) -> list[str]:
    """현재 KOSPI200 구성종목. 구성종목이 아직 없으면(pykrx 로그인 전) 최근 시총 상위 top_n 으로 임시 대체.

    KIS 는 30일치만 주므로 구성종목 확보 전이라도 지금부터 쌓아야 한다.
    """
    cur = kospi200.current_members()
    if len(cur):
        return sorted(cur)
    from src.universe import marketcap

    days = marketcap.saved_days()
    if not days:
        return []
    cap = marketcap.load(max(days))["market_cap"].sort_values(ascending=False)
    log.warning("KOSPI200 구성종목 없음 → %s 시총 상위 %d종목으로 임시 수집", f"{max(days):%Y-%m-%d}", top_n)
    return list(cap.index[:top_n])


def do_update(log, codes: list[str] | None) -> int:
    """KIS 최근 30거래일로 증분. 기존(pykrx) 행이 있는 날은 건드리지 않는다."""
    codes = codes or update_codes(log)
    if len(codes) == 0:
        log.error("대상 종목이 없다. collect_universe.py --backfill 또는 --membership 먼저.")
        return 1
    fails, added = [], 0
    for i, c in enumerate(codes, 1):
        try:
            before = len(flows.load(c))
            df = flows.save(c, flows.fetch_kis(c), source="kis", keep_existing=True)
            added += len(df) - before
        except Exception as e:  # noqa: BLE001
            fails.append((c, str(e)))
            log.warning("%s: %s", c, e)
        if i % 50 == 0 or i == len(codes):
            log.info("[%d/%d] KIS 수급 증분, 새 행 %d", i, len(codes), added)
    log.info("수급 증분(KIS) %d종목, 새 행 %d, 실패 %d", len(codes) - len(fails), added, len(fails))
    return 0 if not fails else 2


def do_verify(log, codes: list[str] | None, n: int = 10) -> int:
    """pykrx 로 받은 과거와 KIS 30일이 겹치는 구간에서 값이 같은지."""
    codes = codes or [c for c in update_codes(log) if "pykrx" in set(flows.load(c).get("source", []))][:n]
    if not codes:
        log.warning("pykrx 수급이 있는 종목이 없어 비교할 수 없다 (--backfill 먼저)")
        return 1
    for c in codes:
        try:
            old = flows.load(c)
            old = old[old["source"] == "pykrx"]
            rep = flows.compare_sources(old, flows.fetch_kis(c))
            log.info("%s\n%s", c, rep.to_string(index=False))
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", c, e)
    return 0


def do_status(log) -> int:
    codes = kospi200.all_members()
    have = [c for c in codes if flows.flow_path(c).exists()]
    print("=" * 60)
    print(f"수급 파일: {len(have)}/{len(codes)}")
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
    print("피처 가중치: 0 (IC 측정 전). lag=1, 분모=20일 거래대금 중앙값 shift(1)")
    print("=" * 60)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--codes", nargs="*")
    a = ap.parse_args(argv)
    log = cli.setup("collect_flows")

    rc = 0
    if a.check:
        rc |= 0 if do_check(log) else 1
    if a.backfill:
        rc |= do_backfill(log, a.years, a.only_missing, a.codes)
    if a.update:
        rc |= do_update(log, a.codes)
    if a.verify:
        rc |= do_verify(log, a.codes)
    if a.status:
        rc |= do_status(log)
    if not any([a.check, a.backfill, a.update, a.verify, a.status]):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
