"""사전 등록 div_bt_v1 실행 (docs/prereg/2026-09-30_dividend_backtest.md).

  python scripts/run_div_bt.py --phase insample
  python scripts/run_div_bt.py --phase holdout --confirm     # 한 번만. div_sig_v1 IC 홀드아웃도 같이 판정
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import _boot  # noqa: F401
import pandas as pd

from src import backtest as bt
from src import cli, config
from src.features import dividend as dv
from src.features.ic import daily_returns
from src.panel import load_panel, universe_mask
from src.universe import marketcap, membership, prices

PREREG_ID = "div_bt_v1"
PERIODS = {"insample": ("2016-06-01", "2025-09-29"), "holdout": ("2025-09-30", None)}
REBAL, TOP, T_IN = 20, 0.20, 2.24


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def run_phase(log, phase: str) -> dict:
    start, end = PERIODS[phase]
    codes = membership.all_members()
    p = load_panel(codes, total_return=True)
    trc, price, volume = p["close"], p["price"], p["volume"]
    ok = bt.eligible(price, universe_mask(price, volume))
    dy = dv.features(price)["dy_ttm"]
    cap = marketcap.cap_panel(codes).reindex(index=price.index, columns=price.columns)
    ones = pd.DataFrame(1.0, index=price.index, columns=price.columns)
    end = end or f"{price.index.max():%Y-%m-%d}"
    runs = {
        "A_ew": bt.run(trc, dy, ok, start, end, rebalance_every=REBAL, top_frac=TOP),
        "B_cw": bt.run(trc, dy, ok, start, end, rebalance_every=REBAL, top_frac=TOP, size=cap),
        "U_ew": bt.run(trc, ones, ok, start, end, rebalance_every=REBAL, top_frac=1.0),
        "U_cw": bt.run(trc, ones, ok, start, end, rebalance_every=REBAL, top_frac=1.0, size=cap),
    }
    k = daily_returns(prices.load_bench("069500")["Close"].to_frame("k"))["k"]
    first = runs["A_ew"].index[runs["A_ew"]["n_hold"] > 0][0]
    days = runs["A_ew"].index[runs["A_ew"].index > first]
    r = {n: x["ret"].loc[days] for n, x in runs.items()}
    r["KODEX200"] = k.reindex(days).fillna(0.0)
    res = {"phase": phase, "window": [f"{days[0]:%Y-%m-%d}", f"{days[-1]:%Y-%m-%d}"], "days": len(days),
           "stats": {n: bt.stats(v) for n, v in r.items()}, "strategies": {}}
    for s, base in (("A_ew", "U_ew"), ("B_cw", "U_cw")):
        ex = bt.excess_test(r[s], r[base])
        st, kd = res["stats"][s], res["stats"]["KODEX200"]
        res["strategies"][s] = {
            "vs_universe": ex, "excess_total": float((1 + r[s]).prod() - (1 + r[base]).prod()),
            "turnover_ann": float(runs[s]["turnover"].loc[days].sum() / (len(days) / 252)),
            "cost_ann": float(runs[s]["cost"].loc[days].sum() / (len(days) / 252)),
            "n_hold": float(runs[s]["n_hold"].loc[days].median()),
            "c1_cagr_ge_kodex": bool(st["CAGR"] >= kd["CAGR"]),
            "c1h_total_ge_kodex": bool(st["total"] >= kd["total"]),
            "c2_t": bool((ex["excess_t"] or 0) >= T_IN),
            "c2h_excess_pos": bool((1 + r[s]).prod() > (1 + r[base]).prod()),
        }
    pd.DataFrame(r).to_csv(config.OUTPUTS / f"prereg_{PREREG_ID}_{phase}_daily.csv", encoding="utf-8-sig")
    return res


def show(res: dict) -> None:
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]} ({res['days']}일)")
    rows = [{"": n, "누적": s["total"], "CAGR": s["CAGR"], "변동성": s["vol"], "샤프": s["sharpe"], "MDD": s["MDD"]}
            for n, s in res["stats"].items()]
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    for n, s in res["strategies"].items():
        v = s["vs_universe"]
        print(f"{n}: 유니버스 대비 초과 연 {v['excess_ann']:+.3f}, NW t {v['excess_t']:.2f} | 회전율 연 {s['turnover_ann']:.2f}, "
              f"비용 연 {s['cost_ann']:.4f}, 보유 {s['n_hold']:.0f}종목")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("run_div_bt")
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    ins_file = config.OUTPUTS / f"prereg_{PREREG_ID}_insample.json"
    if a.phase == "holdout":
        if not a.confirm or lock_path().exists() or not ins_file.exists():
            log.error("홀드아웃: --confirm 필요, 1회만, 인샘플 먼저.")
            return 1
        ins = json.loads(ins_file.read_text(encoding="utf-8"))
        passed = [n for n, s in ins["strategies"].items() if s["c1_cagr_ge_kodex"] and s["c2_t"]]
        if not passed:
            log.error("인샘플 통과 전략 없음 → 홀드아웃(IC 포함)을 쓰지 않는다.")
            return 1
    res = run_phase(log, a.phase)
    res["run_at"] = datetime.now().isoformat(timespec="seconds")
    if a.phase == "insample":
        res["passed"] = [n for n, s in res["strategies"].items() if s["c1_cagr_ge_kodex"] and s["c2_t"]]
        ins_file.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        for n, s in res["strategies"].items():
            print(f"{n}: 기준1 CAGR ≥ KODEX200 {s['c1_cagr_ge_kodex']} | 기준2 t ≥ {T_IN} {s['c2_t']}")
        print(f"인샘플 통과: {res['passed'] or '없음 → 홀드아웃(IC 포함) 사용 안 함'}")
    else:
        res["verdict"] = {n: bool(res["strategies"][n]["c1h_total_ge_kodex"] and res["strategies"][n]["c2h_excess_pos"])
                          for n in passed}
        lock_path().write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print("백테스트 홀드아웃 판정:", res["verdict"])
        import run_div_ic

        run_div_ic.main(["--phase", "holdout", "--confirm"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
