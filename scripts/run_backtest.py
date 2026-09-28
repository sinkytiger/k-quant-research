"""인샘플 백테스트 — 결과를 보기 전에 고정한 4개 변형만 돌린다.

  python scripts/run_backtest.py

고정 설계 (2026-09-28, IC 측정 직후 결정)
- 기간: 2016-06-01 ~ 2025-09-29 (인샘플). 홀드아웃(2025-09-30~)은 보지 않는다.
- 전략: ① low_vol 상위 20%  ② low_vol + flow_corp 순위평균 상위 20%
  × 리밸런싱 20거래일 / 5거래일 → 4개
- 기준선: 유니버스 동일가중(같은 엔진·같은 비용), KODEX200(069500, 비용 없음)
- 체결 t+1 종가, 비용 kr_retail(매수 0.065%, 매도 0.265%), 상장 252거래일 이상
- 판정: 동일가중 대비 월별 초과수익 NW t ≥ 1.96
주의: 주식은 가격수익률(배당 제외)인데 KODEX200 수정가는 분배금 포함(총수익)이다.
가격끼리 비교하려면 KOSPI200 가격지수(bench/KOSPI200)를 본다.
"""
from __future__ import annotations

import argparse
import logging
import sys

import _boot  # noqa: F401
import pandas as pd

from src import backtest as bt
from src import cli, config
from src.features.ic import daily_returns
from src.panel import load_panel, universe_mask
from src.universe import membership, prices

START, END = "2016-06-01", "2025-09-29"


def rank_avg(ok: pd.DataFrame, *feats: pd.DataFrame) -> pd.DataFrame:
    ranks = [f.where(ok).rank(axis=1, pct=True) for f in feats]
    out = ranks[0]
    for r in ranks[1:]:
        out = out + r  # 하나라도 없으면 NaN (둘 다 있는 종목만 후보)
    return out / len(ranks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    log = cli.setup("run_backtest")
    sys.path.insert(0, str(config.ROOT / "scripts"))
    from run_ic import build_features

    codes = membership.all_members()
    p = load_panel(codes)
    close, volume = p["close"], p["volume"]
    ok = bt.eligible(close, universe_mask(close, volume))
    feats = build_features(logging.getLogger("run_ic"), close, volume, codes)

    scores = {
        "low_vol": feats["low_vol"],
        "low_vol+flow_corp": rank_avg(ok, feats["low_vol"], feats["flow_corp"]),
    }
    ones = pd.DataFrame(1.0, index=close.index, columns=close.columns)

    results, daily = [], {}
    for k in (20, 5):
        base = bt.run(close, ones, ok, START, END, rebalance_every=k, top_frac=1.0)
        daily[f"EW|{k}"] = base["ret"]
        results.append({"strategy": "유니버스 동일가중", "rebal": k, **bt.stats(base["ret"]),
                        "turnover_ann": base["turnover"].sum() / (len(base) / 252),
                        "cost_ann": base["cost"].sum() / (len(base) / 252), "n_hold": base["n_hold"].median()})
        for name, sc in scores.items():
            res = bt.run(close, sc, ok, START, END, rebalance_every=k, top_frac=0.2)
            daily[f"{name}|{k}"] = res["ret"]
            results.append({"strategy": name, "rebal": k, **bt.stats(res["ret"]),
                            "turnover_ann": res["turnover"].sum() / (len(res) / 252),
                            "cost_ann": res["cost"].sum() / (len(res) / 252), "n_hold": res["n_hold"].median(),
                            **bt.excess_test(res["ret"], base["ret"])})
            log.info("%s rebal=%d 완료", name, k)

    kodex = prices.load_bench("069500")["Close"]
    kret = daily_returns(kodex.to_frame("k"))["k"]
    kret = kret[(kret.index >= START) & (kret.index <= END)]
    daily["KODEX200"] = kret
    results.append({"strategy": "KODEX200 (비용 없음)", "rebal": 0, **bt.stats(kret)})

    table = pd.DataFrame(results)
    dd = pd.DataFrame(daily)
    yearly = (1 + dd).groupby(dd.index.year).prod() - 1
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    table.to_csv(config.OUTPUTS / "backtest_insample.csv", index=False, encoding="utf-8-sig")
    dd.to_csv(config.OUTPUTS / "backtest_insample_daily.csv", encoding="utf-8-sig")
    pd.set_option("display.width", 200)
    print(table.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print()
    print(yearly.round(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
