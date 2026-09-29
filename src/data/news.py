"""종목 뉴스 제목 수집 — KIS 국내주식 시황/공시 제목 (FHKST01011800).

- 기준 날짜·시각(FID_INPUT_DATE_1 = "00YYYYMMDD", FID_INPUT_HOUR_1 = "HHMMSS")을 주면 그 이전 40건.
  커서를 가장 오래된 기사 시각으로 옮기며 거꾸로 넘겨 받는다.
- 이력: 2026-09 확인 기준 약 1년(2025-10 무렵)까지는 촘촘하고, 그 이전은 듬성듬성해 쓰지 않는다.
- 제목만 온다(본문 없음). 한 기사에 최대 10개 종목코드(iscd1..10)가 붙는다.
- 저장: raw/news/kis/<code>.csv (id, dt, source, provider, category, title, codes). id 로 중복 제거.
- 시각 규칙(기획서): 15:30 KST 이후 기사는 다음 영업일 신호로 본다 — 피처를 만들 때 적용하고, 원본은 그대로 둔다.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

TR = "FHKST01011800"
PATH = "/uapi/domestic-stock/v1/quotations/news-title"
COLS = ["id", "dt", "source", "provider", "category", "title", "codes"]
DENSE_SINCE = "2025-10-01"


def news_dir() -> Path:
    return config.DATA / "raw" / "news" / "kis"


def news_path(code: str) -> Path:
    return news_dir() / f"{code}.csv"


def parse(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        d, t = str(r.get("data_dt", "")), str(r.get("data_tm", "")).zfill(6)
        if len(d) != 8:
            continue
        codes = [str(r.get(f"iscd{i}", "")).strip() for i in range(1, 11)]
        out.append({"id": str(r.get("cntt_usiq_srno", "")).strip(),
                    "dt": pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"),
                    "source": str(r.get("dorg", "")).strip(), "provider": str(r.get("news_ofer_entp_code", "")).strip(),
                    "category": str(r.get("news_lrdv_code", "")).strip(),
                    "title": str(r.get("hts_pbnt_titl_cntt", "")).strip(),
                    "codes": " ".join(c for c in codes if c)})
    df = pd.DataFrame(out, columns=COLS)
    return df[df["id"] != ""]


def fetch_page(code: str, before: pd.Timestamp) -> pd.DataFrame:
    from src import kis

    body, _ = kis.call(TR, PATH, {
        "FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": code, "FID_TITL_CNTT": "",
        "FID_INPUT_DATE_1": "00" + before.strftime("%Y%m%d"), "FID_INPUT_HOUR_1": before.strftime("%H%M%S"),
        "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": "",
    })
    return parse(list(body.get("output") or []))


def fetch(code: str, since, until=None, max_pages: int = 2000) -> pd.DataFrame:
    """since ~ until 사이 기사. until 기본값은 지금. 오래된 쪽으로 넘기다 since 를 지나면 멈춘다."""
    since = pd.Timestamp(since)
    cur = pd.Timestamp(until) if until is not None else pd.Timestamp.now().floor("s")
    seen: set[str] = set()
    parts = []
    for _ in range(max_pages):
        page = fetch_page(code, cur)
        if page.empty:
            break
        new = page[~page["id"].isin(seen)]
        if new.empty:
            cur = cur - pd.Timedelta(seconds=1)  # 같은 초에 40건이 넘게 몰린 경우 한 칸 넘긴다
            continue
        seen.update(new["id"])
        parts.append(new)
        oldest = page["dt"].min()
        if oldest < since:
            break
        cur = min(oldest, cur)
    if not parts:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(parts, ignore_index=True)
    return df[df["dt"] >= since]


def load(code: str) -> pd.DataFrame:
    p = news_path(code)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(p, dtype={"id": str, "codes": str, "provider": str, "category": str},
                       parse_dates=["dt"], encoding="utf-8-sig").fillna({"codes": "", "title": ""})


def save(code: str, new: pd.DataFrame) -> pd.DataFrame:
    old = load(code)
    df = pd.concat([old, new], ignore_index=True) if len(old) else new
    df = df.drop_duplicates("id", keep="last").sort_values("dt")
    news_path(code).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(news_path(code), index=False, encoding="utf-8-sig")
    return df


def update_since(code: str, default_since=DENSE_SINCE, overlap_hours: int = 6) -> pd.Timestamp:
    old = load(code)
    if old.empty:
        return pd.Timestamp(default_since)
    return old["dt"].max() - pd.Timedelta(hours=overlap_hours)
