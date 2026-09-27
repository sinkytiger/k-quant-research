"""피처별 횡단면 IC (스피어만) — 시점별 유니버스(시총 상위 200) + 유동성 필터 안에서.

  python scripts/run_ic.py                     # 인샘플(~2025-09-29), 지평 5·20일
  python scripts/run_ic.py --horizons 1 5 20

규칙 (기획서 5장)
- 홀드아웃(2025-09-30~)은 보지 않는다. --end 기본값이 인샘플 끝이다.
- 순서: load_panel → restrict_universe → 라벨. 라벨은 유니버스 안 종목끼리만 순위.
- 중첩 수익률(h일) IC 의 t 는 Newey-West, lag = 2h.
- 피처는 t 종가까지의 정보, 라벨은 t 종가 → t+h 종가 수익률.
출력: outputs/ic_summary_<end>.csv, outputs/ic_by_year_<end>.csv
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import numpy as np
import pandas as pd

from src import cli, config
from src.data import flows
from src.features import library
from src.features.ic import cross_sectional_ic, forward_returns
from src.panel import load_panel, universe_mask
from src.stats import nw_tstat
from src.universe import marketcap, membership

INSAMPLE_END = "2025-09-29"  # 첫 홀드아웃(2025-09-30~2026-09-04) 직전
INVESTORS = {"기관합계": "inst", "외국인합계": "frgn", "개인": "indiv", "기타법인": "corp"}


def build_features(log, close, volume, codes):
    feats = library.price_features(close)
    cap = marketcap.cap_panel(codes).reindex(index=close.index, columns=close.columns)
    feats["size"] = library.size_feature(cap)
    value = close * volume
    fp = {INVESTORS[k]: flows.flow_panel(codes, k) for k in INVESTORS}
    feats.update(library.flow_features(fp, value))
    log.info("피처 %d개: %s", len(feats), ", ".join(feats))
    return feats


def summarize(ic: pd.Series, h: int) -> dict:
    return {
        "mean_ic": ic.mean(),
        "nw_t": nw_tstat(ic, lags=2 * h),
        "hit": (ic > 0).mean(),
        "icir_ann": ic.mean() / ic.std() * np.sqrt(252 / h) if ic.std() > 0 else np.nan,
        "days": len(ic),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=INSAMPLE_END)
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 20])
    a = ap.parse_args(argv)
    log = cli.setup("run_ic")
    if pd.Timestamp(a.end) > pd.Timestamp(INSAMPLE_END):
        log.warning("--end 가 홀드아웃(%s 이후)을 포함한다. 결과를 보고 규칙을 바꾸면 홀드아웃이 오염된다.", INSAMPLE_END)

    codes = membership.all_members()
    p = load_panel(codes)
    close, volume = p["close"], p["volume"]
    uni = universe_mask(close, volume)
    log.info("패널 %d일 × %d종목, 유니버스 평균 %.0f종목/일", len(close), close.shape[1],
             uni.sum(axis=1)[uni.index >= a.start].mean())
    feats = build_features(log, close, volume, codes)

    rows, yearly = [], []
    for h in a.horizons:
        fwd = forward_returns(close, h)
        fwd = fwd.where(uni.reindex(index=fwd.index, columns=fwd.columns, fill_value=False).astype(bool))
        # 라벨 끝이 인샘플 끝을 넘지 않게: t+h <= end
        dates = fwd.index[(fwd.index >= a.start)]
        last_ok = close.index[close.index <= a.end]
        cutoff = last_ok[-1 - h] if len(last_ok) > h else last_ok[0]
        fwd = fwd.loc[dates[dates <= cutoff]]
        for name, f in feats.items():
            ic = cross_sectional_ic(f, fwd, min_names=30)
            rows.append({"feature": name, "h": h, **summarize(ic, h)})
            by = ic.groupby(ic.index.year).mean()
            yearly.append(pd.Series(by, name=f"{name}|h{h}"))
        log.info("h=%d 완료 (라벨 마지막 기준일 %s)", h, f"{cutoff:%Y-%m-%d}")

    summary = pd.DataFrame(rows).sort_values(["h", "nw_t"], ascending=[True, False])
    by_year = pd.DataFrame(yearly)
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    tag = pd.Timestamp(a.end).strftime("%Y%m%d")
    summary.to_csv(config.OUTPUTS / f"ic_summary_{tag}.csv", index=False, encoding="utf-8-sig")
    by_year.to_csv(config.OUTPUTS / f"ic_by_year_{tag}.csv", encoding="utf-8-sig")
    pd.set_option("display.width", 160)
    print(summary.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print()
    print(by_year.round(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
