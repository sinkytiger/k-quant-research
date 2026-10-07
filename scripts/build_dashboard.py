"""로컬 대시보드 생성: outputs/dashboard.html (한 파일, 외부 요청 없음).

  python scripts/build_dashboard.py

run_daily.bat 마지막에 돈다. 데이터를 모아 JSON 으로 템플릿(src/dashboard/template.html)에 넣는다.
섹션: 데이터 상태 · 시장(KOSPI200 가격지수 / KODEX200 총수익) · 페이퍼 · 연구(IC·사전등록) · 유니버스 모니터
종목 상세는 종목마다 outputs/stocks/<코드>.js 로 따로 쓰고 화면에서 고를 때 불러온다 (src/stock_detail.py).
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


def _lagger():
    """휴장일을 반영한 '영업일 지연' 계산기 (KIS 휴장일 달력 + 확인된 휴장일)."""
    from src.data import market_extra as mx

    cal, hol, today = mx.load_calendar(), frozenset(krx_daily.load_holidays()), pd.Timestamp.today()
    return lambda last: None if last is None else int(mx.trading_lag(last, today, cal, hol))


def data_status() -> dict:
    lag = _lagger()
    rows = []
    for ds, label in (("stk", "유가증권 일별매매"), ("ksq", "코스닥 일별매매"),
                      ("idx_kospi", "KOSPI 지수"), ("etf", "ETF(KODEX200)")):
        days = krx_daily.saved_days(ds)
        last = days[-1] if days else None
        rows.append({"name": f"KRX {label}", "count": len(days),
                     "first": f"{days[0]:%Y-%m-%d}" if days else None,
                     "last": f"{last:%Y-%m-%d}" if last is not None else None,
                     "lag_bdays": lag(last)})
    caps = marketcap.saved_days()
    rows.append({"name": "시가총액 스냅샷", "count": len(caps), "first": f"{caps[0]:%Y-%m-%d}" if caps else None,
                 "last": f"{caps[-1]:%Y-%m-%d}" if caps else None,
                 "lag_bdays": lag(caps[-1]) if caps else None})
    m = membership.load_membership()
    members = membership.all_members(m)
    fl = [flows.load(c) for c in membership.current_members(m)]
    flast = max((f.index.max() for f in fl if len(f)), default=None)
    rows.append({"name": "KIS 수급 (현재 유니버스)", "count": sum(1 for f in fl if len(f)),
                 "first": None, "last": f"{flast:%Y-%m-%d}" if flast is not None else None,
                 "lag_bdays": lag(flast)})
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
    by_year = []
    fy = sorted(config.OUTPUTS.glob("ic_by_year_*.csv"))
    if fy:
        y = pd.read_csv(fy[-1], index_col=0, encoding="utf-8-sig")
        for key, row in y.iterrows():
            feat, h = str(key).split("|h")
            for year, v in row.items():
                if pd.notna(v):
                    by_year.append({"feature": feat, "h": int(h), "year": int(year), "ic": float(v)})
    return {"ic": ic, "ic_file": f[-1].name if f else None, "ledger": ledger(), "div_curve": div_curve(),
            "backtest": backtest, "ic_by_year": by_year}


def _card(name: str, close: pd.Series, n: int = 120, extra: dict | None = None) -> dict:
    c = close.dropna()
    if len(c) < 2:
        return {}
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    return {"name": name, "asof": f"{c.index[-1]:%Y-%m-%d}", "last": last, "chg": last - prev,
            "pct": last / prev - 1, "spark": [round(float(v), 4) for v in c.tail(n)], **(extra or {})}


KEY_FILINGS = ("주요사항", "자사주", "자금조달", "수주", "리스크", "M&A", "투자")


def recent_filings(names: dict, days: int = 7, n: int = 10) -> list[dict]:
    """유니버스 종목의 최근 주요 공시 (분류가 KEY_FILINGS 인 것만, 정정공시·증권사 일상 발행 신고 제외)."""
    from src import stock_detail as sd
    from src.data import dart

    dl = dart.load_all(start=pd.Timestamp.today().normalize() - pd.Timedelta(days=days))
    if dl.empty:
        return []
    cur = set(membership.current_members())
    dl = dl[dl["stock_code"].isin(cur)].copy()
    dl["cat"] = dl["report_nm"].map(dart.classify)
    from src.filings import is_routine
    routine = dl["report_nm"].map(is_routine)
    dl = dl[dl["cat"].isin(KEY_FILINGS) & ~routine].sort_values(["rcept_dt", "rcept_no"], ascending=False)
    dl = dl.drop_duplicates(["rcept_dt", "stock_code", "report_nm"]).head(n)
    return [{"date": f"{r.rcept_dt:%Y-%m-%d}", "code": r.stock_code, "name": names.get(r.stock_code, r.corp_name),
             "title": str(r.report_nm).strip(), "cat": r.cat, "url": sd.DART_VIEW + str(r.rcept_no)} for r in dl.itertuples()]


def home_section(log, top: int = 30, candle_days: int = 100) -> dict:
    """홈 탭: 시장 카드, 순위표, 고른 종목 캔들, 다가오는 배당 일정 (전 거래일 종가 기준)."""
    from src.data import dividends, market_extra as mx

    cards = []
    for mkt, label in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        inv = mx.load_investor(mkt)
        if len(inv):
            last = inv.iloc[-1]
            cards.append(_card(label, inv["index"], extra={"investor": {k: float(last[k]) for k in ("개인", "외국인", "기관")},
                                                           "big": mkt == "KOSPI"}))
    k200 = prices.load_bench("KOSPI200")
    if len(k200):
        cards.append(_card("코스피 200", k200["Close"]))
    for name, (_, _, label) in mx.GLOBAL.items():
        g = mx.load_global(name)
        if len(g):
            cards.append(_card(label, g["Close"], extra={"global": True}))

    # 순위표: 최신 일별매매 스냅샷 (코스피·코스닥·ETF)
    rows = []
    for ds, mk in (("stk", "코스피"), ("ksq", "코스닥"), ("etf", "ETF")):
        days = krx_daily.saved_days(ds)
        if not days:
            continue
        raw = krx_daily.load_snapshots(ds, start=days[-1])
        for r in raw.itertuples():
            close, chg = pd.to_numeric(str(r.TDD_CLSPRC).replace(",", ""), errors="coerce"), \
                pd.to_numeric(str(r.CMPPREVDD_PRC).replace(",", ""), errors="coerce")
            if not close or pd.isna(close) or close <= 0:
                continue
            base = close - (chg if pd.notna(chg) else 0)
            rows.append({"code": str(r.ISU_CD), "name": str(r.ISU_NM), "market": mk, "close": float(close),
                         "pct": float(close / base - 1) if base > 0 else 0.0,
                         "value": float(pd.to_numeric(str(r.ACC_TRDVAL).replace(",", ""), errors="coerce") or 0),
                         "volume": float(pd.to_numeric(str(r.ACC_TRDVOL).replace(",", ""), errors="coerce") or 0),
                         "mcap": float(pd.to_numeric(str(r.MKTCAP).replace(",", ""), errors="coerce") or 0),
                         "asof": f"{days[-1]:%Y-%m-%d}"})
    R = pd.DataFrame(rows)
    # 시장폭: 거래가 있었던 종목 중 오른·내린·보합 (ETF 제외)
    breadth = {}
    for mk in ("코스피", "코스닥"):
        sub = R[(R["market"] == mk) & (R["volume"] > 0)]
        breadth[mk] = {"up": int((sub["pct"] > 0).sum()), "down": int((sub["pct"] < 0).sum()), "flat": int((sub["pct"] == 0).sum())}
    # 외국인·기관 순매수 (유니버스 종목, KIS 수급 최신일)
    cur = sorted(membership.current_members())
    fr = flows.flow_panel(cur, "외국인합계")
    ins = flows.flow_panel(cur, "기관합계")
    flow_day = fr.index.max() if len(fr) else None
    net = pd.DataFrame({"frgn": fr.loc[flow_day] if flow_day is not None else pd.Series(dtype=float),
                        "inst": ins.loc[flow_day] if flow_day is not None else pd.Series(dtype=float)})
    R = R.merge(net, left_on="code", right_index=True, how="left")
    lists = {}
    active = R[R["volume"] > 0]
    for mk in ("전체", "코스피", "코스닥", "ETF"):
        sub = active if mk == "전체" else active[active["market"] == mk]
        lists[mk] = {"value": list(sub.nlargest(top, "value")["code"]), "volume": list(sub.nlargest(top, "volume")["code"]),
                     "up": list(sub.nlargest(top, "pct")["code"]), "down": list(sub.nsmallest(top, "pct")["code"]),
                     "mcap": list(sub.nlargest(top, "mcap")["code"]),
                     "frgn": list(sub.dropna(subset=["frgn"]).nlargest(top, "frgn")["code"]),
                     "inst": list(sub.dropna(subset=["inst"]).nlargest(top, "inst")["code"])}
    used = sorted({c for d in lists.values() for v in d.values() for c in v})
    R = R[R["code"].isin(used)].drop_duplicates("code")

    # 캔들: 주식은 수정주가 파일, ETF 는 스냅샷 원시가
    candles = {}
    etf_codes = set(R.loc[R["market"] == "ETF", "code"])
    if etf_codes:
        ed = krx_daily.saved_days("etf")[-candle_days:]
        eraw = krx_daily.load_snapshots("etf", start=ed[0])
        eraw = eraw[eraw["ISU_CD"].isin(etf_codes)]
        for code, g in eraw.groupby("ISU_CD"):
            g = g.sort_values("BAS_DD")
            num = lambda col: pd.to_numeric(g[col].astype(str).str.replace(",", ""), errors="coerce")  # noqa: E731
            candles[code] = [[d[2:], o, h, l, c, v] for d, o, h, l, c, v in
                             zip(g["BAS_DD"], num("TDD_OPNPRC"), num("TDD_HGPRC"), num("TDD_LWPRC"), num("TDD_CLSPRC"), num("ACC_TRDVOL"))
                             if c and c > 0]
    for code in R.loc[R["market"] != "ETF", "code"]:
        df = prices.load(code).tail(candle_days)
        candles[code] = [[f"{d:%y%m%d}", round(o, 2) if o == o else None, round(h, 2) if h == h else None,
                          round(l, 2) if l == l else None, round(c, 2), int(v) if v == v else 0]
                         for d, o, h, l, c, v in zip(df.index, df["Open"], df["High"], df["Low"], df["Close"], df["Volume"])]

    # 최근·다가오는 배당 (유니버스, 기준일 −30일 ~ +120일). 연말 배당은 보통 11~12월에 공시돼야 나타난다
    today = pd.Timestamp.today().normalize()
    names = membership.load_names()
    upcoming = []
    for c in cur:
        d = dividends.load(c)
        d = d[(d["record_date"] >= today - pd.Timedelta(days=30)) & (d["record_date"] <= today + pd.Timedelta(days=120))]
        for r in d.itertuples():
            upcoming.append({"date": f"{r.record_date:%Y-%m-%d}", "name": names.get(c, c), "code": c,
                             "dps": float(r.dps), "kind": r.kind})
    upcoming = sorted(upcoming, key=lambda x: x["date"], reverse=True)[:10]
    log.info("홈: 카드 %d, 순위 종목 %d, 캔들 %d, 배당 일정 %d", len(cards), len(R), len(candles), len(upcoming))
    return {"cards": [c for c in cards if c], "rows": R.where(R.notna(), None).to_dict("records"), "lists": lists,
            "candles": candles, "flow_day": f"{flow_day:%Y-%m-%d}" if flow_day is not None else None,
            "upcoming": upcoming, "breadth": breadth, "filings": recent_filings(names)}


def map_section(log, top: int = 300) -> dict:
    """업종 지도: 시장별 시총 상위 top 보통주. 크기 = 시가총액, 색 = 기간 수익률 (수정주가)."""
    from src.universe import kis_master

    try:
        sec = kis_master.sectors()
    except Exception as e:  # noqa: BLE001
        log.warning("업종 지도: KIS 업종 마스터 없음 (%s)", e)
        return {}
    out = {}
    for ds, mk in (("stk", "코스피"), ("ksq", "코스닥")):
        days = krx_daily.saved_days(ds)
        if not days:
            continue
        raw = krx_daily.load_snapshots(ds, start=days[-1])
        snap = pd.DataFrame({"code": raw["ISU_CD"].astype(str), "name": raw["ISU_NM"].astype(str),
                             "mcap": raw["MKTCAP"].map(krx_api.to_num), "close": raw["TDD_CLSPRC"].map(krx_api.to_num),
                             "chg": raw["CMPPREVDD_PRC"].map(krx_api.to_num)})
        common = pd.Series([membership.is_common_stock(c, n) for c, n in zip(snap["code"], snap["name"])], index=snap.index)
        snap = snap[common & (snap["close"] > 0)]
        total = float(snap["mcap"].sum())
        pick = snap.nlargest(top, "mcap")
        asof = days[-1]
        ytd0 = pd.Timestamp(year=asof.year, month=1, day=1)
        rows = []
        for r in pick.itertuples():
            base = r.close - (r.chg if pd.notna(r.chg) else 0)
            c = prices.load(r.code)["Close"].dropna() if prices.price_path(r.code).exists() else pd.Series(dtype=float)
            c = c[c.index <= asof]

            def ret(n, c=c):
                return float(c.iloc[-1] / c.iloc[-n - 1] - 1) if len(c) > n else None
            prev_year = c[c.index < ytd0]
            rows.append({"code": r.code, "name": r.name, "sector": sec.get(r.code, "기타"), "mcap": float(r.mcap),
                         "r1d": float(r.close / base - 1) if base > 0 else None, "r1w": ret(5), "r1m": ret(21),
                         "rytd": float(c.iloc[-1] / prev_year.iloc[-1] - 1) if len(prev_year) and len(c) else None})
        out[mk] = {"asof": f"{asof:%Y-%m-%d}", "coverage": float(pick["mcap"].sum()) / total if total else None,
                   "n_all": int(len(snap)), "rows": rows}
        log.info("업종 지도 %s: %d종목, 시총 %.1f%% (%s)", mk, len(rows), 100 * out[mk]["coverage"], f"{asof:%Y-%m-%d}")
    return out


def filings_section(log, days: int = 31) -> dict:
    """공시 피드: 최근 days 일 DART 유가증권 공시 전체 → outputs/filings.js (탭을 열 때 불러온다)."""
    from src import filings
    from src.data import dart

    start = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    dl = dart.load_all(start=start)
    if dl.empty:
        return {}
    cal = krx_daily.saved_days("stk")
    fl = filings.fluc_map(krx_daily.load_snapshots("stk", start=start - pd.Timedelta(days=7)))
    out = filings.build(dl, [d for d in cal if d >= start - pd.Timedelta(days=7)], fl,
                        set(membership.current_members()), membership.load_names())
    meta = {"from": f"{dl['rcept_dt'].min():%Y-%m-%d}", "to": f"{dl['rcept_dt'].max():%Y-%m-%d}",
            "price_asof": f"{cal[-1]:%Y-%m-%d}" if cal else None}
    filings.write(config.OUTPUTS / "filings.js", out, meta)
    n_routine = sum(r[5] for r in out["rows"])
    log.info("공시 피드 %d건 (일상 신고 %d건, %s~%s)", len(out["rows"]), n_routine, meta["from"], meta["to"])
    return {**meta, "n": len(out["rows"])}


def notes_section(log) -> list[dict]:
    """분석 노트: notes/ 의 PDF 목록 (outputs/notes/ 로 복사·미리보기 생성)."""
    from src import notes

    n = notes.import_sources(config.ROOT / "notes", config.ROOT, log)
    if n:
        log.info("노트 원본 폴더에서 새 PDF %d개 가져옴", n)
    rows = notes.scan(config.ROOT / "notes", config.OUTPUTS / "notes", membership.load_names())
    log.info("분석 노트 %d개 (공개 %d)", len(rows), sum(r["public"] for r in rows))
    return rows


def flows_board_section(log) -> dict:
    """수급 탭: 유니버스 종목별 투자자 순매수(1·5·20·60일 합, 연속 일수, 시총 대비) + 시장별 투자자 추이."""
    from src import flow_board as fb
    from src.data import market_extra as mx
    from src.panel import load_panel

    cur = sorted(membership.current_members())
    panels = {key: flows.flow_panel(cur, col) for col, key in fb.INVESTORS.items()}
    if panels["frgn"].empty:
        return {}
    asof = panels["frgn"].index.max()
    sums = {k: fb.window_sums(v) for k, v in panels.items()}
    stk = {k: fb.streaks(v) for k, v in panels.items()}
    close = load_panel(cur)["close"]
    rets = fb.returns(close, asof)
    cap = marketcap.cap_asof(asof, cur)
    names = membership.load_names()
    try:
        from src.universe import kis_master
        sec = kis_master.sectors()
    except Exception:  # noqa: BLE001
        sec = {}
    r4 = lambda x: None if pd.isna(x) else round(float(x), 4)  # noqa: E731
    rows = []
    for c in cur:
        rows.append({"code": c, "name": names.get(c, c), "sector": sec.get(c, "기타"),
                     "mcap": None if pd.isna(cap.get(c)) else float(cap.get(c)),
                     "f": {k: [None if pd.isna(sums[k].at[c, n]) else float(sums[k].at[c, n]) if c in sums[k].index else None
                               for n in fb.WINDOWS] for k in panels},
                     "st": {k: int(stk[k].get(c, 0)) for k in panels},
                     "r": [r4(rets.at[c, n]) if c in rets.index else None for n in fb.WINDOWS]})
    market = {}
    for mkt, label in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        inv = mx.load_investor(mkt)
        if len(inv):
            market[label] = fb.market_series(inv)
    log.info("수급 탭 %d종목 (%s), 시장 %s", len(rows), f"{asof:%Y-%m-%d}", list(market))
    return {"asof": f"{asof:%Y-%m-%d}", "windows": list(fb.WINDOWS), "rows": rows, "market": market}


def stocks_section(log, home: dict, extra: set[str] | None = None) -> dict:
    """종목 상세: 현재 유니버스 + 홈 순위표·업종 지도에 나온 주식. 종목마다 outputs/stocks/<코드>.js 로 따로 쓴다."""
    from src import stock_detail as sd
    from src.data import dart, dividends, news

    snap = {}
    for ds, mk in (("ksq", "코스닥"), ("stk", "코스피")):  # 같은 코드면 코스피(나중)가 이긴다
        days = krx_daily.saved_days(ds)
        if not days:
            continue
        for r in krx_daily.load_snapshots(ds, start=days[-1]).itertuples():
            snap[str(r.ISU_CD)] = {"name": str(r.ISU_NM), "market": mk, "mcap": krx_api.to_num(r.MKTCAP)}
    cur = set(membership.current_members())
    codes = sorted(cur | {r["code"] for r in home.get("rows", []) if r.get("market") != "ETF"} | (extra or set()))
    names = membership.load_names()
    dl = dart.load_all(start=pd.Timestamp.today().normalize() - pd.DateOffset(months=6))
    dl = dl[dl["stock_code"].str.len() == 6]
    by_code = dict(tuple(dl.groupby("stock_code")))
    payloads, index = {}, []
    for c in codes:
        px = prices.load(c) if prices.price_path(c).exists() else pd.DataFrame()
        if px.empty:
            continue
        info = snap.get(c, {"name": names.get(c, c), "market": "코스피", "mcap": None})
        st = sd.price_stats(px["Close"])
        asof = px.index[-1]
        div = sd.dividend_block(dividends.load(c), st.get("close"), asof)
        payloads[c] = {"code": c, "name": info["name"], "market": info["market"], "universe": c in cur,
                       "asof": f"{asof:%Y-%m-%d}", "mcap": info["mcap"], "stats": st, "candles": sd.ohlcv(px),
                       "flows": sd.flow_block(flows.load(c)), "news": sd.news_block(news.load(c)),
                       "dart": sd.dart_block(by_code.get(c, pd.DataFrame())), "div": div}
        index.append({"code": c, "name": info["name"], "market": info["market"], "universe": c in cur,
                      "mcap": info["mcap"]})
    n = sd.write_all(config.OUTPUTS / "stocks", clean(payloads))
    log.info("종목 상세 %d종목 (새로 쓴 파일 %d개, 공시 %s~)", len(index), n,
             f"{dl['rcept_dt'].min():%Y-%m-%d}" if len(dl) else "-")
    return {"list": sorted(index, key=lambda x: -(x["mcap"] or 0)),
            "dart_last": f"{dl['rcept_dt'].max():%Y-%m-%d}" if len(dl) else None}


def quality_section(log, window: int = 60) -> dict:
    """데이터 품질 점검 (최근 window 거래일). 심각도: critical > serious > warning > info."""
    from src import quality as q
    from src.data import news

    cal = krx_daily.saved_days("stk")
    recent = cal[-window:]
    asof = recent[-1]
    names = membership.load_names()
    tidy = krx_daily.tidy_stock(krx_daily.load_snapshots("stk", start=cal[-window - 1]))
    t = q.tidy_returns(tidy)
    t = t[t["Date"] >= recent[0]]
    cur = sorted(membership.current_members())
    value = tidy[tidy["code"].isin(cur)].pivot(index="Date", columns="code", values="Value").loc[recent[0]:]
    net = {who: flows.flow_panel(cur, col).loc[recent[0]:] for col, who in
           (("외국인합계", "외국인"), ("기관합계", "기관"), ("개인", "개인"))}
    bad_caps = marketcap.invalid_files()
    price_last = {c: (prices.load(c).index.max() if prices.price_path(c).exists() else None) for c in cur}
    flow_last = {c: (flows.load(c).index.max() if flows.flow_path(c).exists() else None) for c in cur}
    news_last = {}
    for c in cur:
        df = news.load(c)
        news_last[c] = df["dt"].max().normalize() if len(df) else None
    caldx = pd.DatetimeIndex(cal)
    issues = [
        q.holiday_conflicts(krx_daily.load_holidays(), set(cal)),
        q.price_jumps(t, set(cur)),
        q.flow_exceeds_value(net, value, names),
        q.Issue("무효 시가총액 파일 (전부 0)", "serious" if bad_caps else "ok",
                f"{len(bad_caps)}개" if bad_caps else "없음", [p.name for p in bad_caps][:30]),
        q.missing_days(recent, {ds: set(krx_daily.saved_days(ds)) for ds in ("ksq", "idx_kospi", "etf")}),
        q.stale(price_last, asof, caldx, names, 0, "시세 끊김 (현재 유니버스)", "warning"),
        q.stale(flow_last, asof, caldx, names, 3, "수급 끊김 (현재 유니버스, 3거래일 초과)", "warning"),
        q.stale(news_last, asof, caldx, names, 20, "뉴스 끊김 (현재 유니버스, 20거래일 초과)", "info"),
        q.adjustments(t),
    ]
    out = [i.to_dict() for i in issues]
    counts = {sev: sum(1 for i in out if i["severity"] == sev) for sev in ("critical", "serious", "warning", "info", "ok")}
    log.info("품질 점검 %d개: %s", len(out), counts)
    return {"window": [f"{recent[0]:%Y-%m-%d}", f"{asof:%Y-%m-%d}"], "issues": out, "counts": counts}


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
            "regime": regime_section(log), "quality": quality_section(log), "home": home_section(log)}
    data["map"] = map_section(log)
    data["filings"] = filings_section(log)
    data["notes"] = notes_section(log)
    data["flowboard"] = flows_board_section(log)
    data["stocks"] = stocks_section(log, data["home"], {r["code"] for m in data["map"].values() for r in m["rows"]})
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(clean(data), ensure_ascii=False, default=str))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("대시보드: %s (%.0f KB)", OUT, OUT.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
