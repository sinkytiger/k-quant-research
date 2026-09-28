"""사전 등록 voltarget_v1 실행 (docs/prereg/2026-09-28_voltarget_kodex200.md).

  python scripts/run_voltarget.py --phase insample
  python scripts/run_voltarget.py --phase holdout --confirm     # 한 번만. 잠금 파일

규칙·기간·판정은 문서(기획서 6-4장 값)에 고정돼 있고 여기서 바꾸지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import _boot  # noqa: F401
import pandas as pd

from src import allocation as al
from src import backtest as bt
from src import cli, config
from src.universe import prices

PREREG_ID = "voltarget_v1"
PERIODS = {"insample": ("2016-06-01", "2025-09-29"), "holdout": ("2025-09-30", "2026-09-23")}
TARGET, WINDOW, MDD_RATIO, CAGR_GAP = 0.15, 20, 0.75, 0.03


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def judge(sa: dict, sc: dict) -> dict:
    c1 = abs(sa["MDD"]) <= MDD_RATIO * abs(sc["MDD"])
    c2 = sa["CAGR"] >= sc["CAGR"] - CAGR_GAP
    risk_adj = abs(sa["MDD"]) <= MDD_RATIO * abs(sc["MDD"]) and sa["sharpe"] >= sc["sharpe"] * 1.2
    return {"criterion_1_mdd": bool(c1), "criterion_2_cagr": bool(c2), "pass": bool(c1 and c2),
            "risk_adjusted_reference_only": bool(risk_adj)}


def run_phase(phase: str) -> dict:
    start, end = PERIODS[phase]
    px = prices.load_bench("069500")["Close"]
    ret = px.pct_change(fill_method=None).dropna()
    a = al.voltarget(ret, start, end, TARGET, WINDOW)
    days = a.index[1:]  # 첫날은 종가 진입이라 두 비교군 모두 둘째 날부터
    ra, rc = a["ret"].loc[days], ret.loc[days]
    sa, sc = bt.stats(ra), bt.stats(rc)
    res = {"phase": phase, "window": [f"{days[0]:%Y-%m-%d}", f"{days[-1]:%Y-%m-%d}"], "days": len(days),
           "A_voltarget": sa, "C_kodex200": sc,
           "A_mean_exposure": float(a["exposure"].loc[days].mean()),
           "A_cost_ann": float(a["cost"].loc[days].sum() / (len(days) / 252)),
           **judge(sa, sc)}
    pd.DataFrame({"A": ra, "C": rc, "exposure": a["exposure"].loc[days]}).to_csv(
        config.OUTPUTS / f"prereg_{PREREG_ID}_{phase}_daily.csv", encoding="utf-8-sig")
    return res


def show(res: dict) -> None:
    rows = [{"": lab, "누적": res[k]["total"], "CAGR": res[k]["CAGR"], "변동성": res[k]["vol"],
             "샤프": res[k]["sharpe"], "MDD": res[k]["MDD"]}
            for k, lab in (("A_voltarget", "A 변동성 타게팅"), ("C_kodex200", "C KODEX200 보유"))]
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]} ({res['days']}일)")
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"평균 노출 {res['A_mean_exposure']:.2f}, 비용 연 {res['A_cost_ann']:.4f}")
    print(f"기준1 |MDD| ≤ 75%: {res['criterion_1_mdd']} | 기준2 CAGR ≥ 지수−3%p: {res['criterion_2_cagr']} "
          f"→ {'통과' if res['pass'] else '불통과'}  (참고: 위험조정 기준 {res['risk_adjusted_reference_only']})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("run_voltarget")
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
        if not ins.exists() or not json.loads(ins.read_text(encoding="utf-8"))["pass"]:
            log.error("진행 조건 불충족(인샘플 통과 필요). 홀드아웃을 쓰지 않는다.")
            return 1
    res = run_phase(a.phase)
    res["run_at"] = datetime.now().isoformat(timespec="seconds")
    out = lock_path() if a.phase == "holdout" else config.OUTPUTS / f"prereg_{PREREG_ID}_{a.phase}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    show(res)
    if a.phase == "insample":
        print(f"홀드아웃 진행 조건: {'충족' if res['pass'] else '불충족 → 중단'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
