"""DART 재무 주요계정 수집 (코스피·코스닥 보통주 전체, 다중회사 API 로 100개씩).

  python scripts/collect_fin.py --backfill --start 2023   # 2023년 1분기부터 (약 400 호출)
  python scripts/collect_fin.py --update                   # 올해 보고서 + 작년 사업보고서 다시 (정정 반영)
  python scripts/collect_fin.py --members           # 유니버스 이력 종목(상폐 포함) 중 빠진 회사만 모든 보고서에서 받아 합친다
  python scripts/collect_fin.py --status
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.data import dart_fin as f
from src.universe import krx_daily, membership


def targets() -> list[str]:
    """최신 스냅샷의 코스피·코스닥 보통주 중 DART 고유번호가 있는 회사."""
    codes = {}
    for ds in ("stk", "ksq"):
        days = krx_daily.saved_days(ds)
        if days:
            raw = krx_daily.load_snapshots(ds, start=days[-1])
            codes.update(dict(zip(raw["ISU_CD"].astype(str), raw["ISU_NM"].astype(str))))
    cc = f.corp_codes().set_index("stock_code")["corp_code"]
    return sorted({cc[c] for c, n in codes.items() if membership.is_common_stock(c, n) and c in cc.index})


def member_corps() -> list[str]:
    """시점별 유니버스(시총 상위 200) 이력 종목의 고유번호 — 상장폐지된 회사도 연구에 필요하다 (생존편향)."""
    from src.data import dart

    cc = dict(zip(*f.corp_codes()[["stock_code", "corp_code"]].T.values))
    dl = dart.load_all()
    cc2 = dict(zip(dl["stock_code"], dl["corp_code"]))
    out = {cc.get(c) or cc2.get(c) for c in membership.all_members()}
    return sorted(x for x in out if x)


def merge_members(log, todo, corps) -> int:
    """이미 받은 보고서 파일에 corps 중 빠진 회사만 받아서 합친다."""
    for y, r in todo:
        old = pd.read_csv(f.path(y, r), dtype=str, encoding="utf-8-sig") if f.path(y, r).exists() else pd.DataFrame(columns=["corp_code"])
        miss = sorted(set(corps) - set(old["corp_code"]))
        if not miss:
            continue
        skipped = []
        try:
            new = f.fetch(y, r, miss, skipped=skipped)
        except f.DartFinError as e:
            log.error("%d %s: %s", y, r, e)
            return 2
        if len(new):
            f.save(y, r, pd.concat([old, new.astype(str)], ignore_index=True))
        log.info("%d %s: 유니버스 이력 회사 %d개 요청 → %d개 받음", y, r, len(miss), new["corp_code"].nunique() if len(new) else 0)
    return 0


def slots(start: int, today: pd.Timestamp) -> list[tuple[int, str]]:
    """(연도, 보고서) — 분기 말에서 40일 지난 것만 (그 전엔 아직 나오지 않는다)."""
    ends = {"11013": (3, 31), "11012": (6, 30), "11014": (9, 30), "11011": (12, 31)}
    out = []
    for y in range(start, today.year + 1):
        for r, (m, d) in ends.items():
            if pd.Timestamp(year=y, month=m, day=d) + pd.Timedelta(days=40) <= today:
                out.append((y, r))
    return out


def collect(log, todo, corps, force: bool) -> int:
    for y, r in todo:
        if not force and f.path(y, r).exists():
            continue
        skipped = []
        try:
            df = f.fetch(y, r, corps, skipped=skipped)
        except f.DartFinError as e:
            log.error("%d %s: %s", y, r, e)
            return 2
        f.save(y, r, df)
        n = df["stock_code"].nunique() if len(df) else 0
        log.info("%d %s: %d개 회사%s", y, r, n, f" (응답 오류로 건너뜀 {len(skipped)}개: {skipped[:5]})" if skipped else "")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--members", action="store_true", help="유니버스 이력 종목(상폐 포함) 보충")
    ap.add_argument("--start", type=int, default=2023)
    a = ap.parse_args(argv)
    log = cli.setup("collect_fin")
    today = pd.Timestamp.today().normalize()
    rc = 0
    if a.backfill or a.update:
        corps = targets()
        log.info("대상 회사 %d개", len(corps))
        if a.backfill:
            rc |= collect(log, slots(a.start, today), corps, force=False)
        if a.update:  # 최근 4개 보고서는 정정·늦은 제출을 반영해 다시 받는다
            rc |= collect(log, slots(today.year - 1, today)[-4:], corps, force=True)
    if a.members:
        rc |= merge_members(log, slots(a.start, today), member_corps())
    if a.status:
        df = f.load_all()
        print("=" * 60)
        if df.empty:
            print("재무: 없음")
        else:
            g = df.groupby(["year", "q"])["stock_code"].nunique()
            print("재무 주요계정 (연도, 분기 → 회사 수):")
            print(g.to_string())
        print("=" * 60)
    if not (a.backfill or a.update or a.status or a.members):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
