"""사전 등록 fin_factor_v1 실행 (docs/prereg/2026-10-07_fin_factors.md).

  python scripts/run_fin_ic.py --phase insample
  python scripts/run_fin_ic.py --phase holdout --confirm     # 등록 문서 규칙: 백테스트 사전 등록·인샘플 통과 뒤에만, 한 번
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

import _boot  # noqa: F401
import numpy as np
import pandas as pd

from run_div_ic import residual
from src import backtest as bt
from src import cli, config
from src.data import dart_fin
from src.features import fundamental as fu
from src.features import library
from src.features.ic import cross_sectional_ic, forward_returns
from src.panel import load_panel, universe_mask
from src.stats import nw_tstat
from src.universe import marketcap, membership

PREREG_ID = "fin_factor_v1"
PERIODS = {"insample": ("2016-06-01", "2025-09-29"), "holdout": ("2025-09-30", None)}
FEATURES, HORIZONS = ("bp", "ep", "roe", "qv"), (20, 60)
T_IN, T_OUT, MIN_NAMES = 2.73, 1.65, 30


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def run_phase(log, phase: str) -> dict:
    start, end = PERIODS[phase]
    codes = membership.all_members()
    p = load_panel(codes, total_return=True)
    tr_close, price, volume = p["close"], p["price"], p["volume"]
    ok = bt.eligible(price, universe_mask(price, volume))
    cap = marketcap.cap_panel(codes).reindex(index=price.index, columns=price.columns)
    fin = dart_fin.load_all()
    f = fu.features(fin, cap)
    feats = {k: f[k].where(ok) for k in ("bp", "ep", "roe")}
    feats["qv"] = fu.qv_from(feats["bp"], feats["roe"])
    lowvol = library.price_features(price)["low_vol"]
    size = library.size_feature(cap)
    end = end or f"{price.index.max():%Y-%m-%d}"
    res = {"phase": phase, "window": [start, end], "tests": []}
    # 생존편향 점검: 유니버스 종목·날짜 중 재무(bp 원천인 자본)가 있는 비율, 재무가 전혀 없는 이력 종목
    win = price.index[(price.index >= start) & (price.index <= end)]
    okw = ok.loc[win]
    have = f["bp"].loc[win].notna() | f["ep"].loc[win].notna()
    res["coverage"] = {"member_days_with_fin": float((have & okw).sum().sum() / max(1, okw.sum().sum())),
                       "members_without_any_fin": sorted(c for c in price.columns if okw[c].any() and not have[c].any())}
    for h in HORIZONS:
        fwd = forward_returns(tr_close, h).where(ok)
        days = win[:-h] if len(win) > h else win[:0]
        fwd = fwd.loc[days]
        for name in FEATURES:
            x = feats[name].loc[days]
            ic = cross_sectional_ic(x, fwd, min_names=MIN_NAMES)
            ric = cross_sectional_ic(residual(x, [lowvol.loc[days], size.loc[days]]), fwd, min_names=MIN_NAMES)
            res["tests"].append({"feature": name, "h": h, "days": int(len(ic)),
                                 "names_median": float(x.notna().sum(axis=1).median()),
                                 "first_day": f"{ic.index.min():%Y-%m-%d}" if len(ic) else None,
                                 "mean_ic": float(ic.mean()), "nw_t": nw_tstat(ic, lags=2 * h),
                                 "ref_resid_ic": float(ric.mean()), "ref_resid_t": nw_tstat(ric, lags=2 * h)})
            log.info("%s h=%d 완료", name, h)
    return res


def show(res: dict) -> None:
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]}")
    print(pd.DataFrame(res["tests"]).to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
    c = res.get("coverage", {})
    print(f"커버리지: 유니버스 종목·날짜 중 재무 있음 {c.get('member_days_with_fin', 0):.1%}, "
          f"재무가 전혀 없는 이력 종목 {len(c.get('members_without_any_fin', []))}개 {c.get('members_without_any_fin', [])[:10]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("run_fin_ic")
    logging.getLogger("run_ic").setLevel(logging.WARNING)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    ins_file = config.OUTPUTS / f"prereg_{PREREG_ID}_insample.json"
    chosen = []
    if a.phase == "holdout":
        bt_ok = config.OUTPUTS / "prereg_fin_bt_v1_insample.json"
        if not a.confirm or lock_path().exists() or not ins_file.exists() or not bt_ok.exists() \
                or not json.loads(bt_ok.read_text(encoding="utf-8")).get("passed"):
            log.error("홀드아웃: --confirm, 1회만, 인샘플 IC 와 비용 포함 백테스트(fin_bt_v1) 인샘플 통과가 먼저다.")
            return 1
        ins = json.loads(ins_file.read_text(encoding="utf-8"))
        chosen = [(t["feature"], t["h"], np.sign(t["mean_ic"])) for t in ins["tests"] if abs(t["nw_t"]) >= T_IN]
        if not chosen:
            log.error("인샘플 |t| ≥ %.2f 없음 → 중단.", T_IN)
            return 1
    res = run_phase(log, a.phase)
    res["run_at"] = datetime.now().isoformat(timespec="seconds")
    if a.phase == "insample":
        res["to_holdout"] = [f"{t['feature']}|h{t['h']}" for t in res["tests"] if abs(t["nw_t"]) >= T_IN]
        ins_file.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print(f"인샘플 |t| ≥ {T_IN}: {res['to_holdout'] or '없음 → 중단'}")
    else:
        res["verdict"] = {}
        for f, h, sgn in chosen:
            t = next(x for x in res["tests"] if x["feature"] == f and x["h"] == h)
            res["verdict"][f"{f}|h{h}"] = bool(np.sign(t["mean_ic"]) == sgn and t["nw_t"] * sgn >= T_OUT)
        lock_path().write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print("홀드아웃 판정:", res["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
