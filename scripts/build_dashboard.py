"""로컬 대시보드 생성: outputs/dashboard.html (한 파일, 외부 요청 없음).

  python scripts/build_dashboard.py

run_daily.bat 마지막에 돈다. 데이터를 모아 JSON 으로 템플릿(src/dashboard/template.html)에 넣는다.
섹션: 데이터 상태 · 시장(KOSPI200 가격지수 / KODEX200 총수익) · 페이퍼 · 연구(IC·사전등록) · 유니버스 모니터
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime

import _boot  # noqa: F401
import pandas as pd

from src import cli, config, krx_api
from src.data import flows
from src.universe import krx_daily, marketcap, membership, prices

TEMPLATE = config.ROOT / "src" / "dashboard" / "template.html"
OUT = config.OUTPUTS / "dashboard.html"


def clean(x):
    """JSON 에 NaN/inf 가 들어가지 않게."""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def series_points(s: pd.Series) -> list:
    s = s.dropna()
    return [[f"{d:%Y-%m-%d}", round(float(v), 4)] for d, v in s.items()]


def data_status() -> dict:
    today = pd.Timestamp.today().normalize()
    rows = []
    for ds, label in (("stk", "유가증권 일별매매"), ("ksq", "코스닥 일별매매"),
                      ("idx_kospi", "KOSPI 지수"), ("etf", "ETF(KODEX200)")):
        days = krx_daily.saved_days(ds)
        last = days[-1] if days else None
        rows.append({"name": f"KRX {label}", "count": len(days),
                     "first": f"{days[0]:%Y-%m-%d}" if days else None,
                     "last": f"{last:%Y-%m-%d}" if last is not None else None,
                     "lag_bdays": int(len(pd.bdate_range(last, today)) - 1) if last is not None else None})
    caps = marketcap.saved_days()
    rows.append({"name": "시가총액 스냅샷", "count": len(caps), "first": f"{caps[0]:%Y-%m-%d}" if caps else None,
                 "last": f"{caps[-1]:%Y-%m-%d}" if caps else None,
                 "lag_bdays": int(len(pd.bdate_range(caps[-1], today)) - 1) if caps else None})
    m = membership.load_membership()
    members = membership.all_members(m)
    fl = [flows.load(c) for c in membership.current_members(m)]
    flast = max((f.index.max() for f in fl if len(f)), default=None)
    rows.append({"name": "KIS 수급 (현재 유니버스)", "count": sum(1 for f in fl if len(f)),
                 "first": None, "last": f"{flast:%Y-%m-%d}" if flast is not None else None,
                 "lag_bdays": int(len(pd.bdate_range(flast, today)) - 1) if flast is not None else None})
    have = {p.stem for p in config.PRICES_DIR.glob("*.csv")} if config.PRICES_DIR.exists() else set()
    safe = bool(m) and all(c in have for c in members)
    return {"rows": rows, "universe_snapshots": len(m), "universe_members_ever": len(members),
            "universe_current": len(membership.current_members(m)),
            "universe_last": f"{max(m):%Y-%m-%d}" if m else None,
            "survivorship_safe": safe, "holidays": len(krx_daily.load_holidays()),
            "krx_calls_today": krx_api.used_today(), "last_run": last_run_status()}


def last_run_status() -> dict:
    logs = sorted(config.LOGS.glob("daily_*.log"))
    if not logs:
        return {}
    text = logs[-1].read_text(encoding="utf-8", errors="replace")
    steps = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and "] exit" in line:
            name, code = line[1:].split("] exit")
            steps[name] = int(code.strip() or 0)
    return {"file": logs[-1].name, "steps": steps}


def market() -> dict:
    out = {}
    for key, name in (("KOSPI200", "KOSPI200 (가격지수)"), ("069500", "KODEX200 (분배금 포함)")):
        df = prices.load_bench(key)
        if len(df):
            out[key] = {"name": name, "points": series_points(df["Close"])}
    return out


def paper_section() -> dict:
    d = config.OUTPUTS / "paper"
    gates = json.loads((d / "gate.json").read_text(encoding="utf-8")) if (d / "gate.json").exists() else {}
    defs = json.loads((config.ROOT / "paper" / "portfolios.json").read_text(encoding="utf-8"))
    ports = []
    for name, spec in defs.items():
        f = d / f"nav_{name}.csv"
        nav = pd.read_csv(f, index_col=0, parse_dates=True) if f.exists() else pd.DataFrame()
        bench_nav = (1 + nav["bench_ret"]).cumprod() if len(nav) else pd.Series(dtype=float)
        ports.append({"name": name, "description": spec.get("description"), "start": spec.get("start"),
                      "weights": spec.get("weights"), "benchmark": spec.get("benchmark"),
                      "gate": gates.get(name, {}),
                      "nav": series_points(nav["nav"]) if len(nav) else [],
                      "bench": series_points(bench_nav) if len(nav) else []})
    return {"portfolios": ports}


def research() -> dict:
    ic = []
    f = sorted(config.OUTPUTS.glob("ic_summary_*.csv"))
    if f:
        df = pd.read_csv(f[-1])
        ic = df.to_dict("records")
    prereg = []
    for p in sorted(config.OUTPUTS.glob("prereg_*_insample.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        pid = p.stem.replace("prereg_", "").replace("_insample", "")
        hold = config.OUTPUTS / f"prereg_{pid}_holdout.json"
        strat = r.get("A_strategy") or r.get("A_voltarget") or {}
        bench = r.get("C_kodex200") or {}
        passed = r.get("pass") if "pass" in r else (strat.get("total", 0) >= bench.get("total", 0))
        prereg.append({"id": pid, "window": r.get("window"), "strategy": strat, "bench": bench,
                       "insample_pass": bool(passed), "holdout_used": hold.exists()})
    bt = config.OUTPUTS / "backtest_insample.csv"
    backtest = pd.read_csv(bt).to_dict("records") if bt.exists() else []
    return {"ic": ic, "ic_file": f[-1].name if f else None, "prereg": prereg, "backtest": backtest}


def monitor(log) -> dict:
    """현재 유니버스 종목의 최신 피처. 투자 권유가 아니라 연구 신호 표시."""
    sys.path.insert(0, str(config.ROOT / "scripts"))
    from run_ic import build_features

    from src.panel import load_panel

    cur = sorted(membership.current_members())
    if not cur:
        return {"rows": []}
    p = load_panel(cur)
    close, volume = p["close"], p["volume"]
    feats = build_features(logging.getLogger("run_ic"), close, volume, cur)
    last = close.index.max()
    names = membership.load_names()
    cap = marketcap.cap_asof(last, cur)
    ret1 = close.pct_change(fill_method=None).loc[last]
    lv = feats["low_vol"].loc[last]
    lv_pct = lv.rank(pct=True)
    rows = []
    for c in cur:
        rows.append({"code": c, "name": names.get(c, c), "close": close.at[last, c] if c in close else None,
                     "ret_1d": ret1.get(c), "mcap_trn": (cap.get(c) or 0) / 1e12,
                     "vol_60": -lv.get(c) * math.sqrt(252) if pd.notna(lv.get(c)) else None,
                     "high_vol_20": bool(lv_pct.get(c, 1) <= 0.2),
                     "rel_60": feats["rel_60"].at[last, c], "flow_corp": feats["flow_corp"].at[last, c],
                     "flow_frgn": feats["flow_frgn"].at[last, c], "flow_inst": feats["flow_inst"].at[last, c]})
    log.info("모니터 %d종목 (%s)", len(rows), f"{last:%Y-%m-%d}")
    return {"asof": f"{last:%Y-%m-%d}", "rows": rows}


def main(argv=None) -> int:
    log = cli.setup("build_dashboard")
    data = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "status": data_status(),
            "market": market(), "paper": paper_section(), "research": research(), "monitor": monitor(log)}
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(clean(data), ensure_ascii=False, default=str))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("대시보드: %s (%.0f KB)", OUT, OUT.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
