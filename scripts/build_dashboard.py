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
from pathlib import Path

import _boot  # noqa: F401
import numpy as np
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


def _state(lag, ok: int, warn: int, unit: str = "영업일") -> tuple[str, str]:
    """지연 → (상태, 표시). ok 이하 정상, warn 이하 경고, 그 위 심각, 없음은 치명."""
    if lag is None:
        return "critical", "없음"
    if lag <= ok:
        return "good", "정상"
    return ("warning" if lag <= warn else "serious"), f"{lag}{unit} 지연"


def data_status() -> dict:
    """데이터 상태 표: 항목마다 정상 지연 기준이 다르다 (예: 신용잔고는 결제일 공시라 3영업일, 백업은 8일)."""
    import os

    from src.data import dart, market_extra as mx, news, short_credit as sc
    from src.data import dart_fin

    lag = _lagger()
    today = pd.Timestamp.today().normalize()
    rows = []

    def add(group, name, last, count=None, first=None, ok=1, warn=3, unit="영업일", days=None, note=""):
        last = pd.Timestamp(last) if last is not None else None
        v = days if days is not None else (lag(last) if last is not None else None)
        st, label = _state(v, ok, warn, unit)
        rows.append({"group": group, "name": name, "last": f"{last:%Y-%m-%d}" if last is not None else None,
                     "count": count, "first": f"{pd.Timestamp(first):%Y-%m-%d}" if first is not None else None,
                     "lag_bdays": v, "state": st, "label": label, "note": note})

    for ds, label in (("stk", "유가증권 일별매매"), ("ksq", "코스닥 일별매매"), ("idx_kospi", "코스피 지수"),
                      ("idx_kosdaq", "코스닥 지수"), ("etf", "ETF 일별매매")):
        days = krx_daily.saved_days(ds)
        add("KRX", label, days[-1] if days else None, len(days), days[0] if days else None)
    caps = marketcap.saved_days()
    add("KRX", "시가총액 스냅샷", caps[-1] if caps else None, len(caps), caps[0] if caps else None)

    m = membership.load_membership()
    members = membership.all_members(m)
    cur = membership.current_members(m)
    lasts = lambda xs: max((x for x in xs if x is not None), default=None)  # noqa: E731
    fl = [flows.load(c) for c in cur]
    add("KIS", "수급 (유니버스)", lasts(f.index.max() for f in fl if len(f)), sum(1 for f in fl if len(f)))
    for kind, label, ok, warn, note in (("short", "공매도 (유니버스)", 1, 3, ""),
                                         ("credit", "신용잔고 (유니버스)", 3, 5, "결제일 기준 공시라 매매일보다 2거래일가량 늦다")):
        ds_ = [sc.load(kind, c) for c in cur if sc.path(kind, c).exists()]
        add("KIS", label, lasts(d.index.max() for d in ds_ if len(d)), len(ds_), ok=ok, warn=warn, note=note)
    nl = []
    for c in cur:
        d = news.load(c)
        if len(d):
            nl.append(d["dt"].max().normalize())
    add("KIS", "뉴스 제목 (유니버스)", lasts(nl), len(nl))
    inv = mx.load_investor("KOSPI")
    add("KIS", "시장별 투자자", inv.index.max() if len(inv) else None, len(inv))
    g = mx.load_global("SPX")
    add("KIS", "해외 지수·환율", g.index.max() if len(g) else None, len(g), ok=2, warn=4, note="미국 장은 한국 시간 다음 날 아침에 끝난다")
    cal = mx.load_calendar()
    cov = (cal.index.max() - today).days if len(cal) else None
    rows.append({"group": "KIS", "name": "휴장 달력", "last": f"{cal.index.max():%Y-%m-%d}" if len(cal) else None, "count": len(cal),
                 "first": None, "lag_bdays": None, "state": "good" if cov is not None and cov >= 7 else "warning",
                 "label": f"{cov}일 앞까지" if cov is not None else "없음", "note": "앞으로 7일 이상 있어야 정상"})

    dl = dart.load_all(start=today - pd.Timedelta(days=40))
    add("DART", "공시 목록", dl["rcept_dt"].max() if len(dl) else None, len(dl), ok=1, warn=3)
    fin = dart_fin.load_all()
    if len(fin):
        per = int((fin["year"] * 4 + fin["q"]).max())
        n_latest = fin[(fin["year"] * 4 + fin["q"]) == per]["stock_code"].nunique()
        # 기대 분기 = 분기 말 + 45일(사업보고서는 90일) 이 지난 가장 최근 분기
        exp = None
        for y in (today.year, today.year - 1):
            for q in (4, 3, 2, 1):
                end = pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)
                if end + pd.Timedelta(days=90 if q == 4 else 46) <= today:
                    exp = y * 4 + q if exp is None else max(exp, y * 4 + q)
        lab = lambda P: f"{(P - 1) // 4}.{(P - 1) % 4 + 1}Q"  # noqa: E731
        ok_ = exp is None or per >= exp
        rows.append({"group": "DART", "name": "재무 주요계정", "last": f"{fin['rcept_dt'].max():%Y-%m-%d}", "count": n_latest,
                     "first": lab(int((fin["year"] * 4 + fin["q"]).min())), "lag_bdays": None,
                     "state": "good" if ok_ else "warning", "label": f"{lab(per)} 까지" + ("" if ok_ else f" ({lab(exp)} 미반영)"),
                     "note": "매주 토요일 갱신. 건수 = 최신 분기 보고 회사 수"})
    else:
        add("DART", "재무 주요계정", None)

    from src.data import dividends
    mt = [dividends.div_path(c).stat().st_mtime for c in cur if dividends.div_path(c).exists()]
    last_div = pd.Timestamp.fromtimestamp(max(mt)).normalize() if mt else None
    add("기타", "배당 일정 (월 1회)", last_div, len(mt), days=(today - last_div).days if last_div is not None else None,
        ok=35, warn=45, unit="일")
    nav = sorted((config.OUTPUTS / "paper").glob("nav_*.csv"))
    nav_last = max((pd.read_csv(f, index_col=0, parse_dates=True).index.max() for f in nav), default=None) if nav else None
    add("기타", "페이퍼 NAV", nav_last, len(nav))
    notes = sorted((config.ROOT / "notes").rglob("*.pdf")) if (config.ROOT / "notes").exists() else []
    rows.append({"group": "기타", "name": "분석 노트", "last": max((f"{pd.Timestamp.fromtimestamp(f.stat().st_mtime):%Y-%m-%d}" for f in notes), default=None),
                 "count": len(notes), "first": None, "lag_bdays": None, "state": "good", "label": "정상", "note": "notes/ 의 PDF 수"})
    dests = [Path(x.strip()) for x in (os.environ.get("KQ_BACKUP_DIR") or str(Path(os.environ.get("OneDrive", str(Path.home()))) / "KQuantBackup")).split(";") if x.strip()]
    for d in dests:
        zips = sorted(d.glob("kquant-data-*.zip")) if d.exists() else []
        last_z = pd.Timestamp.fromtimestamp(zips[-1].stat().st_mtime).normalize() if zips else None
        add("기타", f"데이터 백업 ({d.drive or d.anchor}{'구글' if 'G:' in str(d) else 'OneDrive' if 'OneDrive' in str(d) else ''})",
            last_z, len(zips), days=(today - last_z).days if last_z is not None else None, ok=8, warn=15, unit="일",
            note="매주 토요일 자동 백업")

    run = last_run_status()
    if run:
        fails = [k for k, v in run["steps"].items() if v != 0]
        rows.append({"group": "기타", "name": "일일 배치 (마지막 실행)", "last": f"{run['file'][6:10]}-{run['file'][10:12]}-{run['file'][12:14]}",
                     "count": len(run["steps"]), "first": None, "lag_bdays": None, "state": "warning" if fails else "good",
                     "label": f"실패 {', '.join(fails)}" if fails else "정상",
                     "note": "실패한 단계는 다음 날 배치나 수동 실행으로 다시 돈다" if fails else ""})

    have = {p_.stem for p_ in config.PRICES_DIR.glob("*.csv")} if config.PRICES_DIR.exists() else set()
    safe = bool(m) and all(c in have for c in members)
    return {"rows": rows, "universe_snapshots": len(m), "universe_members_ever": len(members),
            "universe_current": len(cur), "universe_last": f"{max(m):%Y-%m-%d}" if m else None,
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

    # 캔들: ETF 만 스냅샷 원시가로 (주식은 화면이 종목 상세 파일 stocks/<코드>.js 의 일봉을 불러 쓴다)
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


_PX: dict = {}
_BAND: dict = {}


def band_prepare(codes: list[str]) -> None:
    """종목 상세 PER·PBR 밴드: 시점 맞춘 TTM 순이익·자본(fin_factor_v1 과 같은 규칙) → 수정주가 기준 주당 값."""
    if "C" not in _BAND or "eps" in _BAND:
        return
    from src.data import dart_fin
    from src.features import fundamental as fu

    C, cap = _BAND["C"], _BAND["cap"]
    cols = [c for c in codes if c in C.columns]
    fin = dart_fin.load_all()
    fin = fin[fin["stock_code"].isin(cols)]
    if fin.empty:
        _BAND.update(eps=pd.DataFrame(), bps=pd.DataFrame())
        return
    sp = fu.step_panels(fu.known_table(fin), C.index, cols)
    shares = cap[cols].where(cap[cols] > 0) / C[cols]  # 수정주가 기준 주식 수
    _BAND.update(eps=sp["ni_ttm"] / shares, bps=sp["equity"].where(sp["equity"] > 0) / shares)


def band_block(code: str, step: int = 5) -> dict:
    """주 단위(5거래일) [날짜, 종가, EPS, BPS] — 화면에서 배수 선을 그린다."""
    eps, bps = _BAND.get("eps"), _BAND.get("bps")
    if eps is None or eps.empty or code not in eps.columns:
        return {}
    C = _BAND["C"][code]
    idx = C.index[::-1][::step][::-1]  # 마지막 날을 꼭 넣는다
    r2 = lambda x: None if x != x else round(float(x), 2)  # noqa: E731
    e, b, c = eps[code].reindex(idx), bps[code].reindex(idx), C.reindex(idx)
    if e.notna().sum() < 10 and b.notna().sum() < 10:
        return {}
    return {"d": [f"{x:%y%m%d}" for x in idx], "c": [r2(x) for x in c], "eps": [r2(x) for x in e], "bps": [r2(x) for x in b]}


def price_stats(H: pd.DataFrame, L: pd.DataFrame, C: pd.DataFrame) -> dict:
    """스크리너용 종목별 가격 지표 (수정가): 1·3·12개월 수익률, 52주 범위 위치, 60일 변동성."""
    c = C.ffill()
    last = c.iloc[-1]

    def ret(n):
        return (last / c.iloc[-n - 1] - 1) if len(c) > n else pd.Series(dtype=float)
    hmax, lmin = H.iloc[-250:].max(), L.iloc[-250:].min()
    pos = ((last - lmin) / (hmax - lmin)).where(hmax > lmin)
    vol = C.pct_change(fill_method=None).iloc[-60:].std() * (252 ** 0.5)
    df = pd.DataFrame({"r1m": ret(21), "r3m": ret(63), "r1y": ret(250), "pos52": pos, "vol60": vol})
    df.loc[C.iloc[-250:].notna().sum() < 240, ["r1y", "pos52"]] = float("nan")
    return {code: row for code, row in df.iterrows()}


def highlow_section(log, hist_days: int = 250) -> dict:
    """52주 신고가·신저가: 코스피·코스닥 보통주, 오늘 목록 + 최근 hist_days 거래일 개수."""
    from src import highlow as hl

    days = krx_daily.saved_days("stk")
    if len(days) < hl.LOOKBACK + hist_days:
        return {}
    start = days[-(hl.LOOKBACK + hist_days + 5)]
    parts = []
    for ds, mk in (("stk", "코스피"), ("ksq", "코스닥")):
        raw = krx_daily.load_snapshots(ds, start=start)
        if len(raw):
            raw = raw.assign(MKT_NM=mk)
            parts.append(krx_daily.tidy_stock(raw))
    tidy = pd.concat(parts, ignore_index=True)
    last_names = tidy.sort_values("Date").groupby("code").last()
    common = [c for c, n in last_names["name"].items() if membership.is_common_stock(c, n)]
    tidy = tidy[tidy["code"].isin(common)]
    d = hl.adjusted(tidy)
    H, L, C = (d.pivot(index="Date", columns="code", values=v) for v in ("aH", "aL", "aC"))
    _BAND.update(C=C, cap=d.pivot(index="Date", columns="code", values="MarketCap").reindex_like(C))
    hi, lo, pmax, pmin = hl.flags(H, L)
    _PX.update(price_stats(H, L, C))
    try:
        from src.universe import kis_master
        sec = kis_master.sectors()
    except Exception:  # noqa: BLE001
        sec = {}
    last = last_names.loc[common]
    info = {c: {"name": r["name"], "market": r["market"], "sector": sec.get(c, "기타"),
                "mcap": None if pd.isna(r["MarketCap"]) else float(r["MarketCap"])} for c, r in last.iterrows()}
    out = {"asof": f"{H.index[-1]:%Y-%m-%d}", "lookback": hl.LOOKBACK,
           "counts": hl.daily_counts(hi, lo, last["market"], hist_days),
           "hi": hl.today_rows("hi", hi.iloc[-1], H.iloc[-1], pmax.iloc[-1], C, info),
           "lo": hl.today_rows("lo", lo.iloc[-1], L.iloc[-1], pmin.iloc[-1], C, info),
           "n_eligible": int(pmax.iloc[-1].notna().sum())}
    log.info("52주 신고가 %d · 신저가 %d (%s, 대상 %d종목)", len(out["hi"]), len(out["lo"]), out["asof"], out["n_eligible"])
    return out


def etf_detail_section(log, etf_rows: list[dict], days: int = 250) -> None:
    """ETF 상세: ETF 마다 outputs/etfs/<코드>.js. 표 행(etf_rows)에 기초지수명·괴리율·추적 차이를 붙인다."""
    from src import etf_detail as ed
    from src import stock_detail as sd

    cal = krx_daily.saved_days("etf")
    if len(cal) < 2 or not etf_rows:
        return
    t = ed.tidy(krx_daily.load_snapshots("etf", start=cal[-(days + 3)] if len(cal) > days + 3 else cal[0]))
    cats = {r["code"]: r.get("category") for r in etf_rows}
    payloads = {}
    for code, g in t.groupby("code", sort=False):
        if code not in cats:
            continue
        one = ed.one(g, days - 1, leveraged=cats[code] == "레버리지·인버스")
        if one:
            payloads[code] = one
    n = sd.write_all(config.OUTPUTS / "etfs", clean(payloads), var="KQ_ETF")
    for r in etf_rows:
        p = payloads.get(r["code"])
        if p:
            r["index_name"] = p["index_name"]
            r["prem_20d"] = p["prem_20d_abs"]
            r["track_1y"] = (p["track_1y"] or {}).get("diff")
    log.info("ETF 상세 %d개 (새로 쓴 파일 %d개)", len(payloads), n)


_VAL = {}


def _valuation():
    """(분기 손익 표, 종목별 지표) — 한 번만 계산해 종목 상세·밸류에이션 탭이 같이 쓴다."""
    if "m" not in _VAL:
        from src import valuation as v
        from src.data import dart_fin

        fin = dart_fin.load_all()
        if fin.empty:
            _VAL.update(q=pd.DataFrame(), m=pd.DataFrame())
            return _VAL["q"], _VAL["m"]
        q = v.quarterly(v.wide(fin))
        mc = {}
        for ds in ("stk", "ksq"):
            days = krx_daily.saved_days(ds)
            if days:
                r = krx_daily.load_snapshots(ds, start=days[-1])
                mc.update(dict(zip(r["ISU_CD"].astype(str), r["MKTCAP"].map(krx_api.to_num))))
        _VAL.update(q=q, m=v.metrics(q, mc))
    return _VAL["q"], _VAL["m"]


def valuation_section(log, data_ref: dict | None = None) -> dict:
    """밸류에이션 탭: 전 종목 지표 → outputs/valuation.js (탭을 열 때 불러온다)."""
    q, m = _valuation()
    data_ref = data_ref or {}
    if m.empty:
        return {}
    names = membership.load_names()
    try:
        from src.universe import kis_master
        sec = kis_master.sectors()
    except Exception:  # noqa: BLE001
        sec = {}
    mk = {}
    for ds, label in (("ksq", "코스닥"), ("stk", "코스피")):
        days = krx_daily.saved_days(ds)
        if days:
            r = krx_daily.load_snapshots(ds, start=days[-1])
            mk.update({str(c): (label, str(n)) for c, n in zip(r["ISU_CD"], r["ISU_NM"])})
    r4 = lambda x: None if x is None or x != x else round(float(x), 4)  # noqa: E731
    cols = ["per", "pbr", "roe", "opm", "rev_yoy", "op_yoy", "debt", "ni_ttm_chg"]
    xcols = ["r1m", "r3m", "r1y", "pos52", "vol60", "frgn20", "inst20", "dy"]
    # 수급(20일 순매수 ÷ 시총)·배당수익률은 유니버스 종목만 있다
    extra = {}
    fb = data_ref.get("flowboard") or {}
    wi = (fb.get("windows") or []).index(20) if 20 in (fb.get("windows") or []) else None
    for r in fb.get("rows", []):
        if wi is not None and r.get("mcap"):
            extra[r["code"]] = {"frgn20": (r["f"]["frgn"][wi] or 0) / r["mcap"], "inst20": (r["f"]["inst"][wi] or 0) / r["mcap"]}
    from src.data import dividends
    asof = pd.Timestamp(krx_daily.saved_days("stk")[-1])
    for c in membership.current_members():
        dv = dividends.load(c)
        px = _PX.get(c)
        cap = m["mcap"].get(c) if c in m.index else None
        if len(dv) and px is not None:
            ttm = dv[(dv["record_date"] > asof - pd.Timedelta(days=365)) & (dv["record_date"] <= asof)]["dps"].sum()
            close = prices.load(c)["Close"].dropna()
            if ttm and len(close):
                extra.setdefault(c, {})["dy"] = float(ttm / close.iloc[-1])
    rows = []
    for c, r in m.iterrows():
        if c not in mk:
            continue
        rows.append([c, mk[c][1] or names.get(c, c), mk[c][0], sec.get(c, "기타"), r4(r["mcap"]), r["label"],
                     *[r4(r[k]) for k in cols], int(bool(r["loss"])),
                     *[r4((_PX.get(c, {}) if k in ("r1m", "r3m", "r1y", "pos52", "vol60") else extra.get(c, {})).get(k)) for k in xcols]])
    meta = {"cols": ["code", "name", "market", "sector", "mcap", "label", *cols, "loss", *xcols],
            "asof": f"{krx_daily.saved_days('stk')[-1]:%Y-%m-%d}"}
    out = config.OUTPUTS / "valuation.js"
    out.write_text("window.KQ_VAL=" + json.dumps(clean({**meta, "rows": rows}), ensure_ascii=False, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    log.info("밸류에이션 %d종목 (PER 있음 %d, 파일 %.0fKB)", len(rows), sum(1 for x in rows if x[6] is not None), out.stat().st_size / 1024)
    return {**meta, "n": len(rows)}


def fin_block(code: str) -> dict:
    """종목 상세 실적 카드: 지표 + 최근 8분기 [라벨, 매출, 영업이익, 순이익]."""
    from src import valuation as v

    q, m = _valuation()
    if m.empty or code not in m.index:
        return {}
    r = m.loc[code]
    keys = ("label", "per", "pbr", "roe", "opm", "rev_yoy", "op_yoy", "debt", "rev_ttm", "op_ttm", "ni_ttm", "loss")
    return {**{k: (None if (r[k] is None or (isinstance(r[k], float) and r[k] != r[k])) else (bool(r[k]) if k == "loss" else r[k]))
               for k in keys}, "rcept": None if pd.isna(r["rcept_dt"]) else f"{r['rcept_dt']:%Y-%m-%d}", "hist": v.history(q, code, 8)}


def _sectors() -> dict:
    try:
        from src.universe import kis_master
        return kis_master.sectors()
    except Exception:  # noqa: BLE001
        return {}


def _snap_info() -> dict:
    """최신 스냅샷의 종목코드 → (시장, 이름)."""
    out = {}
    for ds, mk in (("ksq", "코스닥"), ("stk", "코스피")):
        days = krx_daily.saved_days(ds)
        if days:
            r = krx_daily.load_snapshots(ds, start=days[-1])
            out.update({str(c): (mk, str(n)) for c, n in zip(r["ISU_CD"], r["ISU_NM"])})
    return out


def earnings_section(log) -> dict:
    """실적 시즌 보드 → outputs/earnings.js (시장 탭 '실적'을 열 때 불러온다)."""
    from src import earnings as e

    q, m = _valuation()
    if m.empty:
        return {}
    d = e.season_rows(q, m["mcap"].to_dict())
    if d.empty:
        return {}
    info, sec = _snap_info(), _sectors()
    d = d[d.index.isin(list(info))]
    mk = pd.Series({c: info[c][0] for c in d.index})
    sc = pd.Series({c: sec.get(c, "기타") for c in d.index})
    r = lambda x: None if x is None or x != x else round(float(x), 4)  # noqa: E731
    rows = [[c, info[c][1], info[c][0], sec.get(c, "기타"), r(x.mcap), None if pd.isna(x.rcept_dt) else f"{x.rcept_dt:%Y-%m-%d}",
             r(x.rev), r(x.op), r(x.ni), r(x.rev_p), r(x.op_p), r(x.rev_yoy), r(x.op_yoy), x.turn] for c, x in d.iterrows()]

    def summ(g):
        t = e.summary(d, g)
        return {str(k): {"n": int(v.n), "up": r(v.up), "op": r(v.op), "op_p": r(v.op_p), "yoy": r(v.op_sum_yoy)} for k, v in t.iterrows()}
    payload = {"period": e.label(d.attrs["period"]), "cols": ["code", "name", "market", "sector", "mcap", "rcept", "rev", "op", "ni",
                                                              "rev_p", "op_p", "rev_yoy", "op_yoy", "turn"],
               "rows": rows, "by_market": summ(mk), "by_sector": summ(sc), "all": summ(None)}
    out = config.OUTPUTS / "earnings.js"
    out.write_text("window.KQ_EARN=" + json.dumps(clean(payload), ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    log.info("실적 시즌 %s: %d개 회사 (흑자전환 %d, 적자전환 %d)", payload["period"], len(rows),
             int((d["turn"] == "흑자전환").sum()), int((d["turn"] == "적자전환").sum()))
    return {"period": payload["period"], "n": len(rows)}


def mcap_rank_section(log, top: int = 50) -> dict:
    """시가총액 순위 변동: 오늘·1주(5거래일)·1개월(21거래일) 전 순위 (보통주)."""
    from src import market_valuation as mv

    cal = krx_daily.saved_days("stk")
    if len(cal) < 22:
        return {}
    pick = {"now": cal[-1], "w1": cal[-6], "m1": cal[-22]}
    caps = mv.caps_on(list(pick.values()))
    info = _snap_info()
    out = {"asof": f"{cal[-1]:%Y-%m-%d}", "dates": {k: f"{v:%Y-%m-%d}" for k, v in pick.items()}, "markets": {}}
    for mk in ("코스피", "코스닥"):
        ranks = {}
        for k, d in pick.items():
            c = caps[(caps["Date"] == d) & (caps["market"] == mk)].set_index("code")["mcap"].sort_values(ascending=False)
            ranks[k] = (pd.Series(range(1, len(c) + 1), index=c.index), c)
        rk, cap = ranks["now"]
        rows = []
        for code in rk.index[:top]:
            cp_m = ranks["m1"][1].get(code)
            rows.append({"code": code, "name": info.get(code, ("", code))[1], "rank": int(rk[code]),
                         "w1": None if code not in ranks["w1"][0].index else int(ranks["w1"][0][code]),
                         "m1": None if code not in ranks["m1"][0].index else int(ranks["m1"][0][code]),
                         "mcap": float(cap[code]), "chg_m1": None if not cp_m else float(cap[code] / cp_m - 1)})
        out["markets"][mk] = rows
    log.info("시총 순위 변동 (%s 기준, 1주 %s, 1개월 %s)", out["asof"], out["dates"]["w1"], out["dates"]["m1"])
    return out


def rotation_section(log, weeks: int = 12) -> dict:
    """업종 로테이션: 최근 weeks 주 업종별 주간 수익률(주초 시총 가중, 수정주가). 52주 신고가 계산의 수정가를 재사용."""
    from src import market_valuation as mv

    if "C" not in _BAND:
        return {}
    C = _BAND["C"]
    cal = list(C.index)
    ends = cal[::-1][::5][::-1][-(weeks + 1):]  # 마지막 날 포함 5거래일 간격
    caps = mv.caps_on(ends[:-1])
    sec = _sectors()
    out = {"weeks": [f"{d:%m/%d}" for d in ends[1:]], "markets": {}}
    for mk in ("코스피", "코스닥"):
        cols = []
        for a, b in zip(ends[:-1], ends[1:]):
            w = caps[(caps["Date"] == a) & (caps["market"] == mk)].set_index("code")["mcap"]
            w = w[w.index.isin(C.columns)]
            r = (C.loc[b, w.index] / C.loc[a, w.index] - 1)
            ok = r.notna() & (w > 0)
            g = pd.DataFrame({"r": r[ok], "w": w[ok], "s": [sec.get(c, "기타") for c in w[ok].index]})
            cols.append((g["r"] * g["w"]).groupby(g["s"]).sum() / g["w"].groupby(g["s"]).sum() if len(g) else pd.Series(dtype=float))
        tab = pd.concat(cols, axis=1)
        last_w = caps[(caps["Date"] == ends[-2]) & (caps["market"] == mk)]
        wt = last_w.assign(s=[sec.get(c, "기타") for c in last_w["code"]]).groupby("s")["mcap"].sum()
        wt = wt / wt.sum()
        keep = [s for s in tab.index if s != "기타" and wt.get(s, 0) >= 0.005]
        tab = tab.loc[keep]
        recent = tab.iloc[:, -4:].apply(lambda x: float(np.prod(1 + x.fillna(0)) - 1), axis=1).sort_values(ascending=False)
        out["markets"][mk] = {"sectors": list(recent.index), "weight": [round(float(wt.get(s, 0)), 4) for s in recent.index],
                              "ret": [[None if v != v else round(float(v), 4) for v in tab.loc[s]] for s in recent.index]}
    log.info("업종 로테이션 %d주 (%s~%s)", weeks, out["weeks"][0], out["weeks"][-1])
    return out


def calendar_section(log, days: int = 45) -> list[dict]:
    """다가오는 시장 일정: 휴장(KIS 달력), 선물·옵션 만기(둘째 목요일), 코스피200 정기변경, 정기보고서 마감, 배당 기준일."""
    from src.data import dividends
    from src.data import market_extra as mx

    today = pd.Timestamp.today().normalize()
    end = today + pd.Timedelta(days=days)
    cal = mx.load_calendar()
    closed = {d for d, o in cal.items() if not o and d.weekday() < 5}
    known_to = cal.index.max() if len(cal) else today
    ev = []
    for d in sorted(closed):
        if today <= d <= end:
            ev.append({"date": f"{d:%Y-%m-%d}", "kind": "휴장", "title": "증시 휴장"})

    def prev_open(d):
        while d in closed or d.weekday() >= 5:
            d -= pd.Timedelta(days=1)
        return d
    for m in pd.period_range(today.to_period("M"), end.to_period("M"), freq="M"):
        first = pd.Timestamp(year=m.year, month=m.month, day=1)
        thu2 = first + pd.Timedelta(days=(3 - first.weekday()) % 7 + 7)
        d = prev_open(thu2)
        if today <= d <= end:
            quarter = m.month in (3, 6, 9, 12)
            ev.append({"date": f"{d:%Y-%m-%d}", "kind": "만기", "title": "선물·옵션 동시 만기" if quarter else "옵션 만기",
                       "note": "" if d <= known_to else "휴장 여부 미확인"})
            if m.month in (6, 12):
                nxt = d + pd.Timedelta(days=1)
                while nxt in closed or nxt.weekday() >= 5:
                    nxt += pd.Timedelta(days=1)
                ev.append({"date": f"{nxt:%Y-%m-%d}", "kind": "지수", "title": "코스피200 정기변경 반영"})
    for y in range(today.year, end.year + 1):
        for (mm, dd), t in (((3, 31), "사업보고서 제출 마감 (12월 결산)"), ((5, 15), "1분기 보고서 제출 마감"),
                            ((8, 14), "반기 보고서 제출 마감"), ((11, 14), "3분기 보고서 제출 마감")):
            d0 = d = pd.Timestamp(year=y, month=mm, day=dd)
            while d in closed or d.weekday() >= 5:  # 마감일이 주말·휴일이면 다음 영업일
                d += pd.Timedelta(days=1)
            if today <= d <= end:
                ev.append({"date": f"{d:%Y-%m-%d}", "kind": "공시", "title": t,
                           "note": "" if d == d0 else f"{d0:%m/%d} 이 휴일이라 다음 영업일"})
    names = membership.load_names()
    for c in membership.current_members():
        dv = dividends.load(c)
        dv = dv[(dv["record_date"] >= today) & (dv["record_date"] <= end)]
        for r in dv.itertuples():
            ev.append({"date": f"{r.record_date:%Y-%m-%d}", "kind": "배당", "title": f"{names.get(c, c)} {r.kind} 배당 기준일",
                       "note": f"주당 {r.dps:,.0f}원", "code": c})
    ev.sort(key=lambda x: (x["date"], x["kind"]))
    log.info("다가오는 일정 %d건 (~%s)", len(ev), f"{end:%Y-%m-%d}")
    return ev


def short_credit_section(log, days: int = 250) -> dict:
    """공매도·신용잔고 (유니버스): 종목별 최근 지표 + 유니버스 합산 일별 추이 (수급 탭)."""
    from src.data import short_credit as sc

    cur = sorted(membership.current_members())
    names, sec = membership.load_names(), _sectors()
    rows, amt, val, loan = [], {}, {}, {}
    r4 = lambda x: None if x is None or x != x else round(float(x), 4)  # noqa: E731
    for c in cur:
        s_, c_ = sc.load("short", c), sc.load("credit", c)
        if s_.empty and c_.empty:
            continue
        row = {"code": c, "name": names.get(c, c), "sector": sec.get(c, "기타")}
        if len(s_):
            p = s_["short_amt_pct"]
            row.update(s5=r4(p.tail(5).mean()), s20=r4(p.tail(20).mean()), s60=r4(p.tail(60).mean()),
                       s_date=f"{s_.index.max():%Y-%m-%d}")
            v = (s_["short_amt"] * 100 / s_["short_amt_pct"]).where(s_["short_amt_pct"] > 0)
            amt[c], val[c] = s_["short_amt"], v
        if len(c_):
            row.update(loan_rate=r4(c_["loan_rate"].iloc[-1]), loan_amt=r4(c_["loan_amt"].iloc[-1]),
                       loan_chg20=r4(c_["loan_amt"].iloc[-1] / c_["loan_amt"].iloc[-21] - 1) if len(c_) > 21 and c_["loan_amt"].iloc[-21] > 0 else None,
                       gvrt5=r4(c_["loan_gvrt"].tail(5).mean()), c_date=f"{c_.index.max():%Y-%m-%d}")
            loan[c] = c_["loan_amt"]
        px = prices.load(c)["Close"].dropna() if prices.price_path(c).exists() else pd.Series(dtype=float)
        row["r20"] = r4(px.iloc[-1] / px.iloc[-21] - 1) if len(px) > 21 else None
        rows.append(row)
    A, V, L = pd.DataFrame(amt), pd.DataFrame(val), pd.DataFrame(loan)
    agg_s = (A.sum(axis=1) / V.sum(axis=1) * 100).dropna().tail(days) if len(A) else pd.Series(dtype=float)
    agg_l = (L.ffill().sum(axis=1) / 1e12).tail(days) if len(L) else pd.Series(dtype=float)
    log.info("공매도·신용 %d종목 (공매도 ~%s, 신용 ~%s)", len(rows), f"{agg_s.index.max():%Y-%m-%d}" if len(agg_s) else "-",
             f"{agg_l.index.max():%Y-%m-%d}" if len(agg_l) else "-")
    return {"rows": rows, "short": series_points(agg_s), "loan": series_points(agg_l), "ban": ["2023-11-06", "2025-03-30"]}


def sc_block(code: str, days: int = 250) -> dict:
    """종목 상세: 공매도 거래대금 비중(%)·신용 잔고율(%) 1년 + 최신값 (유니버스 종목만)."""
    from src.data import short_credit as sc

    s_, c_ = sc.load("short", code), sc.load("credit", code)
    if s_.empty and c_.empty:
        return {}
    r = lambda x: None if x != x else round(float(x), 3)  # noqa: E731
    out = {}
    if len(s_):
        t = s_.tail(days)
        out["short"] = {"d": [f"{x:%y%m%d}" for x in t.index], "pct": [r(x) for x in t["short_amt_pct"]],
                        "amt": r(t["short_amt"].iloc[-1]), "s5": r(t["short_amt_pct"].tail(5).mean()), "s60": r(t["short_amt_pct"].tail(60).mean())}
    if len(c_):
        t = c_.tail(days)
        out["credit"] = {"d": [f"{x:%y%m%d}" for x in t.index], "rate": [r(x) for x in t["loan_rate"]],
                         "amt": r(t["loan_amt"].iloc[-1]), "gvrt": r(t["loan_gvrt"].iloc[-1]), "date": f"{t.index.max():%Y-%m-%d}"}
    return out


def extra_quality(log, data: dict) -> None:
    """빌드 끝에 붙이는 품질 점검: 재무 이상값, 공매도·신용 끊김, 신용 잔고율 이상, 상세를 건너뛴 신규 ETF."""
    from src import quality as q
    from src.data import short_credit as sc

    names = membership.load_names()
    issues = []
    _, mt = _valuation()
    bad = mt[mt["suspect"]] if len(mt) and "suspect" in mt else pd.DataFrame()
    issues.append(q.Issue("재무 이상값 (단위 오류 의심)", "info" if len(bad) else "ok",
                          f"{len(bad)}개 — 지표에서 뺐다" if len(bad) else "없음",
                          [f"{names.get(c, c)} ({c})" for c in bad.index][:30]))
    cal = krx_daily.saved_days("stk")
    cur = sorted(membership.current_members())
    for kind, label, gap in (("short", "공매도 끊김 (유니버스, 3거래일 초과)", 3), ("credit", "신용잔고 끊김 (유니버스, 6거래일 초과)", 6)):
        cut = cal[-(gap + 1)] if len(cal) > gap else cal[0]
        stale = [c for c in cur if not sc.path(kind, c).exists() or sc.load(kind, c).index.max() < cut]
        issues.append(q.Issue(label, "warning" if stale else "ok", f"{len(stale)}종목" if stale else "없음",
                              [f"{names.get(c, c)} ({c})" for c in stale][:30]))
    hi = []
    for c in cur:
        d = sc.load("credit", c)
        if len(d) and d["loan_rate"].iloc[-1] > 20:
            hi.append(f"{names.get(c, c)} ({c}) {d['loan_rate'].iloc[-1]:.1f}%")
    issues.append(q.Issue("신용 잔고율 20% 초과 (값 확인)", "warning" if hi else "ok", f"{len(hi)}종목" if hi else "없음", hi[:30]))
    etf_rows = (data.get("etf") or {}).get("rows", [])
    have = {f.stem for f in (config.OUTPUTS / "etfs").glob("*.js")}
    miss = [f"{r['name']} ({r['code']})" for r in etf_rows if r["code"] not in have]
    issues.append(q.Issue("ETF 상세 없음 (상장 3거래일 미만)", "info" if miss else "ok", f"{len(miss)}개" if miss else "없음", miss[:30]))
    Q = data.get("quality") or {"issues": [], "counts": {}}
    Q["issues"] = Q.get("issues", []) + [i.to_dict() for i in issues]
    Q["counts"] = {sev: sum(1 for i in Q["issues"] if i["severity"] == sev) for sev in ("critical", "serious", "warning", "info", "ok")}
    data["quality"] = Q
    log.info("추가 품질 점검 %d개: %s", len(issues), {i.check: i.severity for i in issues})


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
    band_prepare(codes)
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
                       "dart": sd.dart_block(by_code.get(c, pd.DataFrame())), "div": div, "fin": fin_block(c),
                       "band": band_block(c), "sc": sc_block(c) if c in cur else {}}
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


def market_valuation_section(log) -> dict:
    """시장 전체 PER·PBR·ROE (코스피·코스닥, 주 단위, 2017-04~, 시점 맞춤)."""
    from src import market_valuation as mv
    from src.data import dart_fin
    from src.features import fundamental as fu

    fin = dart_fin.load_all()
    if fin.empty:
        return {}
    cal = krx_daily.saved_days("stk")
    wk = mv.weekly(cal, "2017-04-03")
    caps = mv.caps_on(wk)
    days = pd.DatetimeIndex([d for d in cal if d >= pd.Timestamp("2016-01-01")])
    sp = fu.step_panels(fu.known_table(fin), days, sorted(set(caps["code"])))
    agg = mv.aggregate(caps, sp["ni_ttm"].loc[wk], sp["equity"].loc[wk])
    r = lambda x: None if x != x else round(float(x), 4)  # noqa: E731
    out = {"asof": f"{wk[-1]:%Y-%m-%d}", "markets": {}}
    for mk, g in agg.groupby("market"):
        g = g.sort_values("Date")
        out["markets"][mk] = {"dates": [f"{d:%Y-%m-%d}" for d in g["Date"]],
                              **{k: [r(x) for x in g[k]] for k in ("per", "pbr", "roe", "cover")},
                              "earn": [r(x / 1e12) for x in g["earn"]], "mcap": [r(x / 1e12) for x in g["mcap"]]}
    last = agg[agg["Date"] == wk[-1]].set_index("market")
    log.info("시장 PER·PBR %d주 (%s): %s", len(wk), out["asof"],
             ", ".join(f"{m} PER {row.per:.1f} PBR {row.pbr:.2f}" for m, row in last.iterrows()))
    return out


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


# 첫 화면(홈)에 필요 없는 큰 데이터 — 그 탭을 처음 열 때 data/<키>.js 를 불러온다
LAZY = ("etf", "regime", "research", "market", "mval", "monitor", "mrank", "hcandles", "shortcredit")


def write_lazy(data: dict, keys) -> list[str]:
    d = config.OUTPUTS / "data"
    d.mkdir(parents=True, exist_ok=True)
    done = []
    for k in keys:
        if k not in data:
            continue
        body = json.dumps(clean(data.pop(k)), ensure_ascii=False, default=str, separators=(",", ":"))
        (d / f"{k}.js").write_text(f"(window.KQ_D=window.KQ_D||{{}})[{json.dumps(k)}]={body};\n", encoding="utf-8")
        done.append(k)
    return done


def main(argv=None) -> int:
    log = cli.setup("build_dashboard")
    data = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "status": data_status(),
            "market": market(), "paper": paper_section(), "research": research(), "monitor": monitor(log), "etf": etf_section(log),
            "regime": regime_section(log), "quality": quality_section(log), "home": home_section(log)}
    data["mval"] = market_valuation_section(log)
    data["map"] = map_section(log)
    data["filings"] = filings_section(log)
    data["notes"] = notes_section(log)
    data["flowboard"] = flows_board_section(log)
    data["shortcredit"] = short_credit_section(log)
    data["highlow"] = highlow_section(log)
    data["rotation"] = rotation_section(log)
    data["mrank"] = mcap_rank_section(log)
    data["calendar"] = calendar_section(log)
    etf_detail_section(log, data["etf"].get("rows", []))
    data["valuation"] = valuation_section(log, data)
    data["earnings"] = earnings_section(log)
    data["stocks"] = stocks_section(log, data["home"], {r["code"] for m in data["map"].values() for r in m["rows"]})
    extra_quality(log, data)
    data["hcandles"] = data["home"].pop("candles", {})
    data["lazy"] = write_lazy(data, LAZY)
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(clean(data), ensure_ascii=False, default=str))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    log.info("대시보드: %s (%.0f KB)", OUT, OUT.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
