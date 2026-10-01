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


DOC_URL = "https://github.com/sinkytiger/k-quant-research/blob/main/docs/prereg/"


def _j(name: str) -> dict | None:
    f = config.OUTPUTS / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _best(tests: list[dict], tkey: str, label_keys: tuple[str, str]) -> dict:
    return max(tests, key=lambda t: abs(t.get(tkey) or 0)) if tests else {}


def ledger() -> list[dict]:
    """사전 등록 기록. 결과 파일마다 구조가 달라서 등록별로 요약한다."""
    rows = []
    bt = config.OUTPUTS / "backtest_insample.csv"
    if bt.exists():
        d = pd.read_csv(bt)
        lose = d["excess_t"].dropna()
        rows.append({"date": "09-28", "id": "롱온리 v0", "doc": None,
                     "hypothesis": "저변동성·수급 피처 상위 20% 롱온리 (4개 변형)",
                     "metric": f"동일가중 대비 초과 t {lose.min():+.2f} ~ {lose.max():+.2f}",
                     "status": "fail", "holdout": False})
    r = _j("prereg_capw_exvol_v1_insample.json")
    if r:
        rows.append({"date": "09-28", "id": "capw_exvol_v1", "doc": "2026-09-28_capw_exclude_highvol.md",
                     "hypothesis": "시총가중 − 고변동 20% 제외",
                     "metric": f"누적 {r['A_strategy']['total']:+.1%} vs KODEX200 {r['C_kodex200']['total']:+.1%} · 제외 효과 t {r['A_minus_B']['excess_t']:+.2f}",
                     "status": "pass" if r.get("pass") else "fail"})
    r = _j("prereg_voltarget_v1_insample.json")
    if r:
        a, c = r["A_voltarget"], r["C_kodex200"]
        rows.append({"date": "09-28", "id": "voltarget_v1", "doc": "2026-09-28_voltarget_kodex200.md",
                     "hypothesis": "KODEX200 변동성 타게팅 (0.15 / σ20)",
                     "metric": f"MDD {a['MDD']:.1%} (기준 {0.75 * c['MDD']:.1%} 이내) · CAGR {a['CAGR']:.1%} vs {c['CAGR']:.1%}",
                     "status": "pass" if r.get("pass") else "fail"})
    r = _j("prereg_news_sent_v1_explore.json")
    if r:
        b = _best(r["tests"], "ctrl_nw_t", ("feature", "h"))
        rows.append({"date": "09-29", "id": "news_sent_v1", "doc": "2026-09-29_news_sentiment.md",
                     "hypothesis": "뉴스 제목 감성 (시황 기사 제외, 5일 수익률 통제)",
                     "metric": f"최고 {b.get('feature')} {b.get('h')}일 t {b.get('ctrl_nw_t', 0):+.2f} (기준 2.64)",
                     "status": "pass" if r.get("to_holdout") else "fail"})
    r = _j("prereg_dart_events_v1_insample.json")
    if r:
        b = _best(r["tests"], "nw_t", ("event", "h"))
        rows.append({"date": "09-30", "id": "dart_events_v1", "doc": "2026-09-30_dart_events.md",
                     "hypothesis": "공시 이벤트 (자사주 취득·유상증자·공급계약)",
                     "metric": f"최고 {b.get('event')} {b.get('h')}일 t {b.get('nw_t', 0):+.2f} (기준 2.64) · 반응은 진입 전에 끝남",
                     "status": "pass" if r.get("to_holdout") else "fail"})
    r = _j("prereg_div_sig_v1_insample.json")
    if r:
        dy = {t["h"]: t["nw_t"] for t in r["tests"] if t["feature"] == "dy_ttm"}
        rows.append({"date": "09-30", "id": "div_sig_v1", "doc": "2026-09-30_dividend_signals.md",
                     "hypothesis": "배당수익률 IC (총수익 라벨)",
                     "metric": f"dy_ttm 20일 t {dy.get(20, 0):+.2f} · 60일 t {dy.get(60, 0):+.2f} (기준 2.50)",
                     "status": "pass" if r.get("to_holdout") else "fail"})
    r = _j("prereg_div_bt_v1_insample.json")
    if r:
        a, k = r["stats"]["A_ew"], r["stats"]["KODEX200"]
        t = r["strategies"]["A_ew"]["vs_universe"]["excess_t"]
        rows.append({"date": "09-30", "id": "div_bt_v1", "doc": "2026-09-30_dividend_backtest.md",
                     "hypothesis": "배당 상위 20% 롱온리 (총수익·비용)",
                     "metric": f"동일가중 CAGR {a['CAGR']:.1%} vs KODEX200 {k['CAGR']:.1%} · 유니버스 대비 t {t:+.2f} (기준 2.24)",
                     "status": "pass" if r.get("passed") else "fail"})
    for row in rows:
        if "holdout" not in row:
            pid = row["id"]
            row["holdout"] = (config.OUTPUTS / f"prereg_{pid}_holdout.json").exists()
        row["url"] = DOC_URL + row["doc"] if row.get("doc") else None
    return rows


def div_curve() -> dict:
    f = config.OUTPUTS / "prereg_div_bt_v1_insample_daily.csv"
    if not f.exists():
        return {}
    d = pd.read_csv(f, index_col=0, parse_dates=True)
    names = {"A_ew": "배당 상위 20% 동일가중", "U_cw": "유니버스 시총가중", "KODEX200": "KODEX200"}
    return {k: {"name": v, "points": series_points((1 + d[k]).cumprod() * 100)} for k, v in names.items() if k in d}


def research() -> dict:
    ic = []
    f = sorted(config.OUTPUTS.glob("ic_summary_*.csv"))
    if f:
        df = pd.read_csv(f[-1])
        ic = df.to_dict("records")
    bt = config.OUTPUTS / "backtest_insample.csv"
    backtest = pd.read_csv(bt).to_dict("records") if bt.exists() else []
    return {"ic": ic, "ic_file": f[-1].name if f else None, "ledger": ledger(), "div_curve": div_curve(),
            "backtest": backtest}


def regime_section(log) -> dict:
    """시장 국면: 변동성·시장폭·200일선 위 비율·투자자별 순매수 (전체 이력 + 현재 백분위)."""
    from src import regime
    from src.universe import liquidity

    k = prices.load_bench("KOSPI200")["Close"]
    vol = regime.realized_vol(k, 20) * 100
    br = regime.breadth(krx_daily.load_snapshots("stk"))["ratio"].rolling(20, min_periods=15).mean() * 100
    m = membership.load_membership()
    codes = membership.all_members(m)
    close = prices.panel(codes, "Close")
    mask = liquidity.membership_mask(close.index, close.columns, m)
    ab = regime.above_ma(close, mask, 200) * 100
    fl = {}
    for col, key in (("외국인합계", "frgn"), ("기관합계", "inst"), ("개인", "indiv")):
        f = flows.flow_panel(codes, col).reindex(close.index)
        fl[key] = regime.investor_flow(f, mask).rolling(20, min_periods=15).sum() / 1e12
    start = pd.Timestamp("2016-01-01")
    cut = lambda s: s[s.index >= start]  # noqa: E731
    tiles = {
        "vol": {"value": vol.dropna().iloc[-1], "pct": regime.percentile(cut(vol))},
        "breadth": {"value": br.dropna().iloc[-1], "pct": regime.percentile(cut(br))},
        "above200": {"value": ab.dropna().iloc[-1], "pct": regime.percentile(cut(ab))},
        "frgn": {"value": fl["frgn"].dropna().iloc[-1], "pct": regime.percentile(cut(fl["frgn"]))},
    }
    log.info("국면: 변동성 %.1f%%, 시장폭 %.1f%%, 200일선 위 %.1f%%, 외국인 20일 %.2f조",
             tiles["vol"]["value"], tiles["breadth"]["value"], tiles["above200"]["value"], tiles["frgn"]["value"])
    return {"asof": f"{close.index.max():%Y-%m-%d}", "tiles": tiles,
            "vol": series_points(cut(vol)), "breadth": series_points(cut(br)), "above200": series_points(cut(ab)),
            "flows": {k2: series_points(cut(v)) for k2, v in fl.items()}}


def etf_section(log) -> dict:
    """ETF 성과표·자금 흐름 (전 종목 스냅샷이 있는 구간)."""
    from src import etf

    tidy = etf.load_tidy(start=pd.Timestamp.today() - pd.DateOffset(days=560))
    sm = etf.summary(tidy)
    if sm.empty:
        return {"rows": []}
    n_days = tidy["Date"].nunique()
    full_from = tidy.groupby("Date")["code"].nunique()
    full_from = full_from[full_from > 100].index.min()
    keep = ["code", "name", "category", "aum", "value_20d", "ret_1w", "ret_1m", "ret_3m", "ret_ytd", "ret_1y",
            "flow_1w", "flow_1m", "flow_3m", "flow_1m_pct"]
    rows = sm[[c for c in keep if c in sm]].to_dict("records")
    log.info("ETF %d종목 (%s, 전 종목 스냅샷 %s~, %d일)", len(rows), f"{sm.attrs['asof']:%Y-%m-%d}",
             f"{full_from:%Y-%m-%d}" if pd.notna(full_from) else "-", n_days)
    return {"asof": f"{sm.attrs['asof']:%Y-%m-%d}", "full_from": f"{full_from:%Y-%m-%d}" if pd.notna(full_from) else None,
            "rows": rows}


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
            "market": market(), "paper": paper_section(), "research": research(), "monitor": monitor(log), "etf": etf_section(log),
            "regime": regime_section(log)}
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(clean(data), ensure_ascii=False, default=str))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("대시보드: %s (%.0f KB)", OUT, OUT.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
