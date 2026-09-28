"""페이퍼 트래킹 (기획서 7장).

  python scripts/paper_track.py --update    # NAV 재계산 → outputs/paper/nav_<이름>.csv, gate.json
  python scripts/paper_track.py --report    # 포트폴리오별 성과·게이트 요약

포트폴리오 정의: paper/portfolios.json. 새 전략은 새 이름으로 추가하고 커밋한다(기존 항목 수정 금지).
"""
from __future__ import annotations

import argparse
import json
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli, config, paper


def out_dir():
    return config.OUTPUTS / "paper"


def do_update(log) -> dict:
    defs = paper.load_defs()
    out_dir().mkdir(parents=True, exist_ok=True)
    gates = {}
    for name, d in defs.items():
        port = paper.nav(d["weights"], d["start"])
        bench = paper.series(d["benchmark"])
        bret = bench.pct_change(fill_method=None).reindex(port.index).fillna(0.0)
        if len(port):
            bret.iloc[0] = 0.0  # 첫날은 종가 매수라 벤치도 그날 수익 없음
        port.assign(bench_ret=bret).to_csv(out_dir() / f"nav_{name}.csv", encoding="utf-8-sig")
        g = paper.gate(port, bret)
        g["status"] = d.get("status")
        g["last_date"] = f"{port.index.max():%Y-%m-%d}" if len(port) else None
        gates[name] = g
        log.info("%s: %s일, 누적 %s", name, len(port),
                 f"{g.get('cum_return', 0):+.4f}" if len(port) else "기록 전")
    (out_dir() / "gate.json").write_text(json.dumps(gates, ensure_ascii=False, indent=1, default=float),
                                         encoding="utf-8")
    return gates


def do_report(gates: dict) -> None:
    rows = []
    for name, g in gates.items():
        rows.append({"포트폴리오": name, "상태": g.get("status"), "마지막": g.get("last_date"),
                     "완료 월": g.get("months_complete"), "누적": g.get("cum_return"),
                     "벤치 누적": g.get("bench_cum_return"), "월초과 t": g.get("monthly_excess_t"),
                     "게이트": "통과" if g.get("pass") else "닫힘"})
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    print("게이트 = 완료 6개월 이상 AND 누적 > 벤치 AND 월 초과수익 t ≥ 1.96. 통과해도 모의계좌까지만.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("paper_track")
    gates = None
    if a.update:
        gates = do_update(log)
    if a.report:
        if gates is None:
            p = out_dir() / "gate.json"
            gates = json.loads(p.read_text(encoding="utf-8")) if p.exists() else do_update(log)
        do_report(gates)
    if not (a.update or a.report):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
