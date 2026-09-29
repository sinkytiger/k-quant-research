"""뉴스 제목 감성 (Lexicon, 무료) → 종목별 일간 피처.

규칙 (기획서 3-6·3-7장)
- 사전: lexicon_ko.json (v1, 수익률을 보기 전에 고정). 긴 표현부터 매칭하고 매칭 부분은 지운다.
- 제목 점수 = (긍정 수 − 부정 수) / (긍정 수 + 부정 수), 매칭이 없으면 0 이고 '감성 있음'에서 빠진다.
- 거래일 배정: 15:30 KST 이후 기사는 다음 거래일. 휴장일 기사도 다음 거래일.
  t 일 피처 = t 일에 배정된 기사까지 → t 종가에 알 수 있다(백테스트는 t+1 종가 체결).
- 같은 종목·같은 거래일에 제목이 똑같은 기사(여러 매체 전재)는 한 번만 센다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

LEXICON = Path(__file__).with_name("lexicon_ko.json")
CUTOFF = "15:30"

# 자동 시황 기사(이미 움직인 주가를 전하는 사후 보도) 제외 규칙 v1 — 2026-09-29, 수익률 보기 전에 고정.
# 감성 점수가 과거 수익률(단기 반전 효과)을 다시 재는 것을 막는다.
MARKET_AUTO_V1 = re.compile(
    r"[+\-−]?\d+(?:\.\d+)?\s?%"           # 등락률 숫자 (예: +3.71%)
    r"|상한가|하한가"
    r"|순매수\s?상위|순매도\s?상위|매수\s?상위|매도\s?상위|순매수,\s?도|순매수·순매도"
    r"|\[장중수급포착\]|<유>|특징주"
)


def is_market_auto(title: str) -> bool:
    return bool(MARKET_AUTO_V1.search(str(title)))


@lru_cache(maxsize=1)
def lexicon() -> tuple[list[tuple[str, int]], str]:
    d = json.loads(LEXICON.read_text(encoding="utf-8"))
    terms = [(t, 0) for t in d["neutral"]] + [(t, 1) for t in d["positive"]] + [(t, -1) for t in d["negative"]]
    terms = sorted(set(terms), key=lambda x: -len(x[0]))  # 긴 표현부터
    return terms, d["_meta"]["version"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def score_title(title: str) -> tuple[int, int, float]:
    """(긍정 수, 부정 수, 점수 −1~1)."""
    text = _norm(title)
    pos = neg = 0
    for term, pol in lexicon()[0]:
        while term in text:
            text = text.replace(term, " " * len(term), 1)
            if pol > 0:
                pos += 1
            elif pol < 0:
                neg += 1
    tot = pos + neg
    return pos, neg, (pos - neg) / tot if tot else 0.0


def assign_trade_date(dt: pd.Series, trading_days: pd.DatetimeIndex) -> pd.Series:
    """기사 시각 → 신호 거래일. 15:30 이후·휴장일은 다음 거래일. 마지막 거래일 이후면 NaT."""
    dt = pd.to_datetime(dt)
    day = dt.dt.normalize()
    after = dt.dt.strftime("%H:%M") >= CUTOFF
    day = day + pd.to_timedelta(after.astype(int), unit="D")
    td = pd.DatetimeIndex(trading_days).sort_values()
    pos = td.searchsorted(day.values, side="left")
    out = pd.Series(pd.NaT, index=dt.index, dtype="datetime64[ns]")
    ok = pos < len(td)
    out[ok] = td[pos[ok]]
    return out


def daily_table(articles: pd.DataFrame, trading_days: pd.DatetimeIndex, exclude_auto: bool = True) -> pd.DataFrame:
    """한 종목 기사 → 거래일별 n(기사 수), n_sent(감성 있는 기사 수), score_sum. 자동 시황 기사는 기본 제외."""
    if articles.empty:
        return pd.DataFrame(columns=["n", "n_sent", "score_sum"])
    a = articles.copy()
    a["tday"] = assign_trade_date(a["dt"], trading_days)
    a = a.dropna(subset=["tday"])
    if exclude_auto:
        a = a[~a["title"].map(is_market_auto)]
    a["t_norm"] = a["title"].map(_norm)
    a = a.drop_duplicates(["tday", "t_norm"])
    sc = a["title"].map(score_title)
    a["score"] = sc.map(lambda x: x[2])
    a["has"] = sc.map(lambda x: x[0] + x[1] > 0)
    g = a.groupby("tday")
    return pd.DataFrame({"n": g.size(), "n_sent": g["has"].sum(), "score_sum": g["score"].sum()})


def features(tables: dict[str, pd.DataFrame], trading_days: pd.DatetimeIndex,
             window: int = 5, attn_base: int = 60) -> dict[str, pd.DataFrame]:
    """sent_5d: 최근 window 거래일 감성 점수 합 / 감성 기사 수 (평균 톤, 기사 없으면 NaN)
    attn_5d: log(1 + 최근 window 일 기사 수) − log(1 + 직전 attn_base 일 하루 평균 기사 수 × window) (평소 대비 관심)
    sent_attn: sent_5d × (그날 attn_5d 의 횡단면 백분위) — 관심이 몰린 종목의 톤에 가중
    """
    idx = pd.DatetimeIndex(trading_days)
    n = pd.DataFrame({c: t["n"] for c, t in tables.items()}).reindex(idx).fillna(0)
    ns = pd.DataFrame({c: t["n_sent"] for c, t in tables.items()}).reindex(idx).fillna(0)
    ss = pd.DataFrame({c: t["score_sum"] for c, t in tables.items()}).reindex(idx).fillna(0)
    n_w, ns_w, ss_w = (x.rolling(window, min_periods=1).sum() for x in (n, ns, ss))
    sent = ss_w / ns_w.where(ns_w > 0)
    base = n.shift(window).rolling(attn_base, min_periods=attn_base // 2).mean() * window
    attn = np.log1p(n_w) - np.log1p(base)
    attn_pct = attn.rank(axis=1, pct=True)
    return {"sent_5d": sent, "attn_5d": attn, "sent_attn": sent * attn_pct, "news_n_5d": n_w}
