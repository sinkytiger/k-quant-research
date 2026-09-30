"""사전 등록 dart_events_v1 실행 (docs/prereg/2026-09-30_dart_events.md).

  python scripts/run_event_study.py --phase insample
  python scripts/run_event_study.py --phase holdout --confirm     # 한 번만. 잠금 파일
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import _boot  # noqa: F401
import numpy as np
import pandas as pd

from src import cli, config
from src import events as ev
from src.data import dart
from src.universe import membership, prices

PREREG_ID = "dart_events_v1"
PERIODS = {"insample": ("2016-01-01", "2025-09-29"), "holdout": ("2025-09-30", None)}
HORIZONS = (5, 20)
T_IN, T_OUT, COST = 2.64, 1.65, 0.0033


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def run_phase(log, phase: str) -> dict:
    start, end = PERIODS[phase]
    m = membership.load_membership()
    codes = membership.all_members(m)
    close = prices.panel(codes, "Close")
    bench = prices.load_bench("KOSPI200")["Close"]
    end = end or f"{close.index.max():%Y-%m-%d}"
    evs = ev.extract(dart.load_all(start, end), m, close.index)
    log.info("이벤트 %s", evs["event"].value_counts().to_dict())
    res = {"phase": phase, "window": [start, end], "tests": []}
    for h in HORIZONS:
        cars = ev.car(evs, close, bench, h, start=start, end=end)
        for name in sorted(evs["event"].unique()):
            c = cars[cars["event"] == name] if len(cars) else cars
            s = ev.calendar_t(c)
            res["tests"].append({"event": name, "h": h, **s,
                                 "beats_cost": bool(abs(s.get("mean_car") or 0) > COST)})
    return res


def show(res: dict) -> None:
    df = pd.DataFrame(res["tests"])
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]}")
    cols = ["event", "h", "n_events", "n_months", "mean_car", "nw_t", "event_mean", "event_median", "pre_car_mean", "beats_cost"]
    print(df[[c for c in cols if c in df]].to_string(index=False, float_format=lambda x: f"{x:+.4f}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("run_event_study")
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    ins_file = config.OUTPUTS / f"prereg_{PREREG_ID}_insample.json"
    chosen = []
    if a.phase == "holdout":
        if not a.confirm:
            log.error("홀드아웃은 --confirm 이 있어야 한다 (한 번만).")
            return 1
        if lock_path().exists():
            log.error("이미 실행됨: %s", lock_path())
            print(lock_path().read_text(encoding="utf-8"))
            return 1
        if not ins_file.exists():
            log.error("인샘플을 먼저 실행한다.")
            return 1
        ins = json.loads(ins_file.read_text(encoding="utf-8"))
        chosen = [(t["event"], t["h"], np.sign(t["mean_car"])) for t in ins["tests"]
                  if t.get("nw_t") is not None and abs(t["nw_t"]) >= T_IN]
        if not chosen:
            log.error("인샘플에서 |t| ≥ %.2f 없음 → 홀드아웃을 쓰지 않고 중단.", T_IN)
            return 1
    res = run_phase(log, a.phase)
    res["run_at"] = datetime.now().isoformat(timespec="seconds")
    if a.phase == "insample":
        res["to_holdout"] = [f"{t['event']}|h{t['h']}" for t in res["tests"]
                             if t.get("nw_t") is not None and abs(t["nw_t"]) >= T_IN]
        ins_file.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print(f"홀드아웃 후보(|t| ≥ {T_IN}): {res['to_holdout'] or '없음 → 중단'}")
    else:
        res["verdict"] = {}
        for name, h, sgn in chosen:
            t = next(x for x in res["tests"] if x["event"] == name and x["h"] == h)
            res["verdict"][f"{name}|h{h}"] = bool(np.sign(t["mean_car"]) == sgn and (t["nw_t"] or 0) * sgn >= T_OUT)
        lock_path().write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print("홀드아웃 판정:", res["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
