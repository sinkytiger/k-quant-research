"""대시보드 종목 상세: 종목 하나의 시세·수급·뉴스·공시·배당을 작은 JSON 으로 묶는다.

대시보드 본문(한 파일)에 다 넣으면 너무 커지므로 종목마다 outputs/stocks/<코드>.js 로 따로 쓰고,
화면에서 종목을 고를 때만 <script> 로 불러온다 (file:// 로 열어도 동작).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
FLOW_COLS = {"외국인합계": "frgn", "기관합계": "inst", "개인": "indiv", "기타법인": "corp"}


def _r(x, d: int = 4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, d)


def ohlcv(px: pd.DataFrame, days: int = 250) -> list:
    """[yymmdd, o, h, l, c, v]. 거래정지일(시가 0)은 시가·고가·저가를 종가로 둔다."""
    out = []
    for d, o, h, l, c, v in zip(px.index[-days:], *(px[k].iloc[-days:] for k in ("Open", "High", "Low", "Close", "Volume"))):
        if not c or c != c:
            continue
        ok = o == o and o > 0
        out.append([f"{d:%y%m%d}", _r(o if ok else c, 2), _r(h if ok else c, 2), _r(l if ok else c, 2), _r(c, 2),
                    int(v) if v == v else 0])
    return out


def price_stats(close: pd.Series) -> dict:
    c = close.dropna()
    if len(c) < 2:
        return {}
    last = c.iloc[-1]

    def ret(n):
        return _r(last / c.iloc[-n - 1] - 1) if len(c) > n else None

    y = c.iloc[-250:]
    lr = c.pct_change(fill_method=None).iloc[-60:]
    return {"close": _r(last, 2), "ret_1d": ret(1), "ret_1m": ret(21), "ret_3m": ret(63), "ret_1y": ret(250),
            "hi_52w": _r(y.max(), 2), "lo_52w": _r(y.min(), 2),
            "pos_52w": _r((last - y.min()) / (y.max() - y.min())) if y.max() > y.min() else None,
            "vol_60": _r(lr.std() * math.sqrt(252)) if lr.count() >= 40 else None}


def flow_block(fl: pd.DataFrame, days: int = 60) -> dict:
    """최근 days 거래일 투자자별 순매수(원)와 5·20·60일 합계."""
    if fl.empty:
        return {}
    f = fl[[c for c in FLOW_COLS if c in fl]].rename(columns=FLOW_COLS).iloc[-days:]
    sums = {f"{k}_{n}": _r(f[k].iloc[-n:].sum(), 0) for k in f for n in (5, 20, 60)}
    return {"asof": f"{f.index[-1]:%Y-%m-%d}", "dates": [f"{d:%y%m%d}" for d in f.index],
            **{k: [_r(v, 0) for v in f[k]] for k in f}, "sums": sums}


def news_block(nw: pd.DataFrame, n: int = 15) -> list:
    """최근 제목 n개. 시황 자동기사는 표시만 하고 남긴다 (사전 등록 연구에서는 제외했던 기사)."""
    from src.features import news_sentiment as ns

    if nw.empty:
        return []
    out = []
    for r in nw.sort_values(["dt", "id"], kind="stable").iloc[::-1].itertuples():
        auto = ns.is_market_auto(r.title)
        pos, neg, _ = ns.score_title(r.title)
        out.append({"dt": f"{r.dt:%Y-%m-%d %H:%M}", "title": str(r.title), "source": str(r.source), "auto": auto,
                    "tone": 0 if auto else (1 if pos > neg else -1 if neg > pos else 0)})
        if len(out) >= n:
            break
    return out


INSIDER = "임원ㆍ주요주주특정증권등소유상황보고서"  # 대형주는 이 보고가 목록을 다 덮는다


def dart_block(dl: pd.DataFrame, n: int = 15) -> dict:
    """최근 공시 n건. 임원·주요주주 소유 보고는 건수만 센다."""
    from src.data import dart

    if dl.empty:
        return {"rows": [], "insider": 0}
    ins = dl["report_nm"].str.contains(INSIDER, regex=False)
    dl = dl[~ins].sort_values(["rcept_dt", "rcept_no"]).iloc[::-1].head(n)
    return {"rows": [{"date": f"{r.rcept_dt:%Y-%m-%d}", "title": str(r.report_nm).strip(), "cat": dart.classify(r.report_nm),
                      "url": DART_VIEW + str(r.rcept_no)} for r in dl.itertuples()], "insider": int(ins.sum())}


def dividend_block(dv: pd.DataFrame, close: float | None, asof: pd.Timestamp, n: int = 12) -> dict:
    """기준일 기준 최근 n건과 직전 1년 주당배당금 합 ÷ 현재가 (표시용 근사)."""
    if dv.empty:
        return {"rows": [], "dps_ttm": None, "dy_ttm": None}
    d = dv.sort_values("record_date")
    ttm = d[(d["record_date"] > asof - pd.Timedelta(days=365)) & (d["record_date"] <= asof)]["dps"].sum()
    rows = [{"date": f"{r.record_date:%Y-%m-%d}", "dps": _r(r.dps, 0), "kind": str(r.kind),
             "pay": str(r.pay_date)[:10] if pd.notna(r.pay_date) else None} for r in d.iloc[::-1].head(n).itertuples()]
    return {"rows": rows, "dps_ttm": _r(ttm, 0) if ttm else None,
            "dy_ttm": _r(ttm / close) if ttm and close else None}


def to_js(code: str, payload: dict, var: str = "KQ_STOCK") -> str:
    """<script> 로 불러올 수 있게 전역 객체에 넣는 한 줄."""
    return f"(window.{var}=window.{var}||{{}})[{json.dumps(code)}]={json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n"


def write_all(out_dir: Path, payloads: dict[str, dict], var: str = "KQ_STOCK") -> int:
    """종목 파일을 쓰고, 이번에 없는 옛 파일은 지운다. 내용이 같으면 다시 쓰지 않는다(동기화 부담)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for code, p in payloads.items():
        f = out_dir / f"{code}.js"
        s = to_js(code, p, var)
        if not f.exists() or f.read_text(encoding="utf-8") != s:
            f.write_text(s, encoding="utf-8")
            written += 1
    for f in out_dir.glob("*.js"):
        if f.stem not in payloads:
            f.unlink()
    return written
