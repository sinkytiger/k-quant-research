"""사전 등록 규칙 capw_exvol_v1 실행 (docs/prereg/2026-09-28_capw_exclude_highvol.md).

  python scripts/run_prereg.py --phase insample
  python scripts/run_prereg.py --phase holdout --confirm     # 한 번만. 잠금 파일이 생긴다

규칙·기간·판정 기준은 문서에 고정돼 있고 여기서 바꾸지 않는다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

import _boot  # noqa: F401
import pandas as pd

from src import backtest as bt
from src import cli, config
from src.features.ic import daily_returns
from src.panel import load_panel, universe_mask
from src.universe import marketcap, membership, prices

PREREG_ID = "capw_exvol_v1"
PERIODS = {"insample": ("2016-06-01", "2025-09-29"), "holdout": ("2025-09-30", "2026-09-23")}
REBAL, EXCLUDE_FRAC, MDD_TOL = 20, 0.20, 0.05


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def run_phase(log, phase: str) -> dict:
    start, end = PERIODS[phase]
    sys.path.insert(0, str(config.ROOT / "scripts"))
    from run_ic import build_features

    codes = membership.all_members()
    p = load_panel(codes)
    close, volume = p["close"], p["volume"]
    ok = bt.eligible(close, universe_mask(close, volume))
    feats = build_features(logging.getLogger("run_ic"), close, volume, codes)
    cap = marketcap.cap_panel(codes).reindex(index=close.index, columns=close.columns)

    a = bt.run(close, feats["low_vol"], ok, start, end, rebalance_every=REBAL,
               top_frac=1 - EXCLUDE_FRAC, size=cap)
    ones = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    b = bt.run(close, ones, ok, start, end, rebalance_every=REBAL, top_frac=1.0, size=cap)
    k = daily_returns(prices.load_bench("069500")["Close"].to_frame("k"))["k"]

    # 평가 구간: 첫 체결 다음 날부터 (세 비교군 모두 같은 날짜)
    first_trade = a.index[a["n_hold"] > 0][0]
    days = a.index[a.index > first_trade]
    ra, rb, rc = a["ret"].loc[days], b["ret"].loc[days], k.reindex(days).fillna(0.0)
    res = {
        "phase": phase, "window": [f"{days[0]:%Y-%m-%d}", f"{days[-1]:%Y-%m-%d}"], "days": len(days),
        "A_strategy": bt.stats(ra), "B_capw_all": bt.stats(rb), "C_kodex200": bt.stats(rc),
        "A_minus_C": bt.excess_test(ra, rc), "A_minus_B": bt.excess_test(ra, rb),
        "A_turnover_ann": float(a["turnover"].loc[days].sum() / (len(days) / 252)),
        "A_cost_ann": float(a["cost"].loc[days].sum() / (len(days) / 252)),
        "A_n_hold_median": float(a["n_hold"].loc[days].median()),
    }
    ta, tc = res["A_strategy"]["total"], res["C_kodex200"]["total"]
    ma, mc = res["A_strategy"]["MDD"], res["C_kodex200"]["MDD"]
    res["criterion_1_return"] = bool(ta >= tc)
    res["criterion_2_mdd"] = bool(ma >= mc - MDD_TOL)
    res["pass"] = res["criterion_1_return"] and res["criterion_2_mdd"]
    daily = pd.DataFrame({"A": ra, "B": rb, "C": rc})
    daily.to_csv(config.OUTPUTS / f"prereg_{PREREG_ID}_{phase}_daily.csv", encoding="utf-8-sig")
    return res


def show(res: dict) -> None:
    rows = []
    for key, label in (("A_strategy", "A 전략(시총가중−고변동20%)"), ("B_capw_all", "B 시총가중 전체"),
                       ("C_kodex200", "C KODEX200")):
        s = res[key]
        rows.append({"": label, "누적": s["total"], "CAGR": s["CAGR"], "변동성": s["vol"],
                     "샤프": s["sharpe"], "MDD": s["MDD"]})
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]} ({res['days']}일)")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"A−C 초과 연율 {res['A_minus_C']['excess_ann']:+.3f}, NW t {res['A_minus_C']['excess_t']:.2f} "
          f"({res['A_minus_C']['months']}개월) | A−B {res['A_minus_B']['excess_ann']:+.3f}, t {res['A_minus_B']['excess_t']:.2f}")
    print(f"A 회전율 연 {res['A_turnover_ann']:.2f}, 비용 연 {res['A_cost_ann']:.4f}, 보유 중앙값 {res['A_n_hold_median']:.0f}종목")
    print(f"기준1 누적수익 A≥C: {res['criterion_1_return']} | 기준2 MDD A≥C−5%p: {res['criterion_2_mdd']} "
          f"→ {'통과' if res['pass'] else '불통과'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true", help="홀드아웃 1회 실행 확인")
    a = ap.parse_args(argv)
    log = cli.setup("run_prereg")
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)

    if a.phase == "holdout":
        if not a.confirm:
            log.error("홀드아웃은 --confirm 이 있어야 한다 (한 번만).")
            return 1
        if lock_path().exists():
            log.error("이미 실행됨: %s — 다시 돌리지 않는다.", lock_path())
            print(lock_path().read_text(encoding="utf-8"))
            return 1
        ins = config.OUTPUTS / f"prereg_{PREREG_ID}_insample.json"
        if not ins.exists():
            log.error("인샘플 사전 점검을 먼저 실행한다.")
            return 1
        pre = json.loads(ins.read_text(encoding="utf-8"))
        if pre["A_strategy"]["total"] < pre["C_kodex200"]["total"]:
            log.error("진행 조건 불충족: 인샘플에서 A 누적수익 < KODEX200. 홀드아웃을 쓰지 않고 중단한다.")
            return 1

    res = run_phase(log, a.phase)
    res["run_at"] = datetime.now().isoformat(timespec="seconds")
    out = lock_path() if a.phase == "holdout" else config.OUTPUTS / f"prereg_{PREREG_ID}_{a.phase}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    show(res)
    if a.phase == "insample":
        ok = res["A_strategy"]["total"] >= res["C_kodex200"]["total"]
        print(f"홀드아웃 진행 조건(인샘플 A 누적 ≥ C): {'충족' if ok else '불충족 → 중단'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
