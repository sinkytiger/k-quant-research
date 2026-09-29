"""사전 등록 news_sent_v1 실행 (docs/prereg/2026-09-29_news_sentiment.md).

  python scripts/run_news_ic.py --phase explore
  python scripts/run_news_ic.py --phase holdout --confirm     # 한 번만. 잠금 파일

주 지표: 5일 수익률 통제 IC (날짜별 피처 순위를 직전 5일 수익률 순위에 회귀한 잔차).
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
from src.data import news
from src.features import news_sentiment as ns
from src.features.ic import cross_sectional_ic, forward_returns
from src.panel import load_panel, universe_mask
from src.stats import nw_tstat
from src.universe import membership

PREREG_ID = "news_sent_v1"
PERIODS = {"explore": ("2025-10-01", "2026-04-30"), "holdout": ("2026-05-01", None)}
FEATURES = ("sent_5d", "attn_5d", "sent_attn")
HORIZONS = (1, 5)
T_EXPLORE, T_HOLDOUT, MIN_NAMES = 2.64, 1.65, 30


def lock_path():
    return config.OUTPUTS / f"prereg_{PREREG_ID}_holdout.json"


def residualize(feat: pd.DataFrame, ctrl: pd.DataFrame, min_names: int = MIN_NAMES) -> pd.DataFrame:
    """날짜별로 피처 순위 ~ 통제변수 순위 선형회귀의 잔차."""
    out = pd.DataFrame(np.nan, index=feat.index, columns=feat.columns)
    for d in feat.index:
        y, x = feat.loc[d], ctrl.loc[d] if d in ctrl.index else None
        if x is None:
            continue
        ok = y.notna() & x.notna()
        if ok.sum() < min_names:
            continue
        yr, xr = y[ok].rank(), x[ok].rank()
        b = np.polyfit(xr, yr, 1)
        out.loc[d, ok[ok].index] = yr - (b[0] * xr + b[1])
    return out


def build(log):
    codes = sorted({c for c in membership.all_members() if news.news_path(c).exists()})
    p = load_panel(codes)
    close, volume = p["close"], p["volume"]
    uni = universe_mask(close, volume)
    td = close.index
    tables = {c: ns.daily_table(news.load(c), td) for c in codes}
    feats = ns.features(tables, td)
    ret5 = close / close.shift(5) - 1
    log.info("뉴스 종목 %d, 거래일 %d", len(codes), len(td))
    return close, uni, feats, ret5


def run_phase(log, phase: str) -> dict:
    start, end = PERIODS[phase]
    close, uni, feats, ret5 = build(log)
    end = end or f"{close.index.max():%Y-%m-%d}"
    res = {"phase": phase, "window": [start, end], "tests": []}
    for h in HORIZONS:
        fwd = forward_returns(close, h).where(uni.reindex_like(close).fillna(False).astype(bool))
        days = close.index[(close.index >= start) & (close.index <= end)]
        days = days[:-h] if len(days) > h else days[:0]  # 라벨 끝(t+h)이 구간 안
        fwd = fwd.loc[days]
        for name in FEATURES:
            f = feats[name].where(uni).loc[days]
            raw = cross_sectional_ic(f, fwd, min_names=MIN_NAMES)
            ctl = cross_sectional_ic(residualize(f, ret5.loc[days]), fwd, min_names=MIN_NAMES)
            cov = f.notna().sum(axis=1)
            res["tests"].append({
                "feature": name, "h": h, "days": int(len(ctl)), "names_median": float(cov.median()),
                "ctrl_mean_ic": float(ctl.mean()) if len(ctl) else None,
                "ctrl_nw_t": nw_tstat(ctl, lags=2 * h) if len(ctl) > 2 else None,
                "raw_mean_ic": float(raw.mean()) if len(raw) else None,
                "raw_nw_t": nw_tstat(raw, lags=2 * h) if len(raw) > 2 else None,
            })
            log.info("%s h=%d 완료", name, h)
    return res


def show(res: dict) -> None:
    df = pd.DataFrame(res["tests"])
    print(f"[{res['phase']}] {res['window'][0]} ~ {res['window'][1]}")
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=list(PERIODS), required=True)
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("run_news_ic")
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    explore_file = config.OUTPUTS / f"prereg_{PREREG_ID}_explore.json"

    if a.phase == "holdout":
        if not a.confirm:
            log.error("홀드아웃은 --confirm 이 있어야 한다 (한 번만).")
            return 1
        if lock_path().exists():
            log.error("이미 실행됨: %s", lock_path())
            print(lock_path().read_text(encoding="utf-8"))
            return 1
        if not explore_file.exists():
            log.error("탐색 단계를 먼저 실행한다.")
            return 1
        ex = json.loads(explore_file.read_text(encoding="utf-8"))
        chosen = [(t["feature"], t["h"], np.sign(t["ctrl_mean_ic"])) for t in ex["tests"]
                  if t["ctrl_nw_t"] is not None and abs(t["ctrl_nw_t"]) >= T_EXPLORE]
        if not chosen:
            log.error("탐색에서 |t| ≥ %.2f 인 검정이 없다. 홀드아웃을 쓰지 않고 중단.", T_EXPLORE)
            return 1

    res = run_phase(log, a.phase)
    res["run_at"] = datetime.now().isoformat(timespec="seconds")
    if a.phase == "explore":
        res["to_holdout"] = [f"{t['feature']}|h{t['h']}" for t in res["tests"]
                             if t["ctrl_nw_t"] is not None and abs(t["ctrl_nw_t"]) >= T_EXPLORE]
        explore_file.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print(f"홀드아웃 후보(|주 지표 t| ≥ {T_EXPLORE}): {res['to_holdout'] or '없음 → 중단'}")
    else:
        verdict = {}
        for f, h, sgn in chosen:
            t = next(x for x in res["tests"] if x["feature"] == f and x["h"] == h)
            ok = t["ctrl_mean_ic"] is not None and np.sign(t["ctrl_mean_ic"]) == sgn and (t["ctrl_nw_t"] or 0) * sgn >= T_HOLDOUT
            verdict[f"{f}|h{h}"] = bool(ok)
        res["verdict"] = verdict
        lock_path().write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        show(res)
        print("홀드아웃 판정:", verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
