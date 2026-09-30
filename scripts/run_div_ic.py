"""사전 등록 div_sig_v1 실행 (docs/prereg/2026-09-30_dividend_signals.md).

  python scripts/run_div_ic.py --phase insample
  python scripts/run_div_ic.py --phase holdout --confirm     # 한 번만. 잠금 파일
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

from src import backtest as bt
from src import cli, config
from src.features import dividend as dv
from src.features import library
from src.features.ic import cross_sectional_ic, forward_returns
from src.panel import load_panel, universe_mask
from src.stats import nw_tstat
from src.universe import marketcap, membership

PREREG_ID = "div_sig_v1"
PERIODS = {"insample": ("2016-06-01", "2025-09-29"), "holdout": ("2025-09-30", None)}
FEATURES, HORIZONS = ("dy_ttm", "div_growth"), (20, 60)
T_IN, T_OUT, MIN_NAMES = 2.50, 1.65, 30


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def residual(f: pd.DataFrame, ctrls: list[pd.DataFrame]) -> pd.DataFrame:
    """날짜별 피처 순위를 통제변수 순위들에 다중회귀한 잔차 (참고용)."""
    out = pd.DataFrame(np.nan, index=f.index, columns=f.columns)
    for d in f.index:
        y = f.loc[d]
        X = pd.concat([c.loc[d] for c in ctrls], axis=1)
        ok = y.notna() & X.notna().all(axis=1)
        if ok.sum() < MIN_NAMES:
            continue
        yr = y[ok].rank().to_numpy()
        Xr = np.column_stack([np.ones(ok.sum())] + [X.loc[ok, j].rank().to_numpy() for j in X.columns])
        beta, *_ = np.linalg.lstsq(Xr, yr, rcond=None)
        out.loc[d, ok[ok].index] = yr - Xr @ beta
    return out


def run_phase(log, phase: str) -> dict:
    start, end = PERIODS[phase]
    codes = membership.all_members()
    p = load_panel(codes, total_return=True)
    tr_close, price, volume = p["close"], p["price"], p["volume"]
    ok = bt.eligible(price, universe_mask(price, volume))
    feats = dv.features(price)
    lowvol = library.price_features(price)["low_vol"]
    size = library.size_feature(marketcap.cap_panel(codes).reindex(index=price.index, columns=price.columns))
    end = end or f"{price.index.max():%Y-%m-%d}"
    res = {"phase": phase, "window": [start, end], "tests": []}
    for h in HORIZONS:
        fwd = forward_returns(tr_close, h).where(ok)
        days = price.index[(price.index >= start) & (price.index <= end)]
        days = days[:-h] if len(days) > h else days[:0]
        fwd = fwd.loc[days]
        for name in FEATURES:
            f = feats[name].where(ok).loc[days]
            ic = cross_sectional_ic(f, fwd, min_names=MIN_NAMES)
            ric = cross_sectional_ic(residual(f, [lowvol.loc[days], size.loc[days]]), fwd, min_names=MIN_NAMES)
            res["tests"].append({"feature": name, "h": h, "days": int(len(ic)),
                                 "names_median": float(f.notna().sum(axis=1).median()),
                                 "mean_ic": float(ic.mean()), "nw_t": nw_tstat(ic, lags=2 * h),
                                 "ref_resid_ic": float(ric.mean()), "ref_resid_t": nw_tstat(ric, lags=2 * h)})
            log.info("%s h=%d 완료", name, h)
    return res


def show(res: dict) -> None:
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]}")
    print(pd.DataFrame(res["tests"]).to_string(index=False, float_format=lambda x: f"{x:+.4f}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("run_div_ic")
    logging.getLogger("run_ic").setLevel(logging.WARNING)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    ins_file = config.OUTPUTS / f"prereg_{PREREG_ID}_insample.json"
    chosen = []
    if a.phase == "holdout":
        if not a.confirm or lock_path().exists() or not ins_file.exists():
            log.error("홀드아웃: --confirm 필요, 1회만, 인샘플 먼저.")
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
        print(f"홀드아웃 후보(|t| ≥ {T_IN}): {res['to_holdout'] or '없음 → 중단'}")
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
