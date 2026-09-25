"""투자자별 순매수 수급 (가격과 직교한 첫 번째 축).

- 출처: KIS 종목별 투자자매매동향(일별) FHPTJ04160001. 기준일을 주면 그날까지 30거래일을 준다.
  기준일을 30일씩 거꾸로 옮기며 10년 백필, 매일은 오늘 기준 한 번. 상폐 종목도 나온다.
- 금액 단위: 백만원 → 원으로 저장.
- 저장: flows/<code>.csv (Date, 기관합계, 기타법인, 개인, 외국인합계, source)
- 피처: 순매수 ÷ 최근 20일 거래대금 중앙값(shift 1), 공표 시차 lag=1.
  원화 그대로 쓰면 삼성전자가 늘 1등이다.
- 가중치: 수집만 한다. picks 점수 가중치는 IC 를 재기 전까지 0.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

FLOW_COLS = ["기관합계", "기타법인", "개인", "외국인합계"]
LAG = 1
NORM_WINDOW = 20

KIS_TR = "FHPTJ04160001"
KIS_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
KIS_MAP = {"orgn_ntby_tr_pbmn": "기관합계", "etc_corp_ntby_tr_pbmn": "기타법인",
           "prsn_ntby_tr_pbmn": "개인", "frgn_ntby_tr_pbmn": "외국인합계"}
KIS_UNIT = 1_000_000  # 백만원 → 원


def flow_path(code: str) -> Path:
    return config.FLOWS_DIR / f"{code}.csv"


def parse_kis(rows: list[dict]) -> pd.DataFrame:
    rows = [r for r in rows if r.get("stck_bsop_date")]
    if len(rows) == 0:
        return pd.DataFrame(columns=FLOW_COLS)
    df = pd.DataFrame(rows)
    out = pd.DataFrame(index=pd.to_datetime(df["stck_bsop_date"], format="%Y%m%d"))
    for src, dst in KIS_MAP.items():
        out[dst] = pd.to_numeric(df[src].astype(str).str.replace(",", ""), errors="coerce").values * KIS_UNIT
    out.index.name = "Date"
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out[FLOW_COLS]


CUTOFF = "15:40"  # KIS: 당일 날짜 조회는 00:00~15:40 에 막힌다 (OPSQ2001 TIME LIMIT)


def last_complete_day(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """조회해도 되는 마지막 날짜. 15:40 전이면 어제."""
    now = now or pd.Timestamp.now()
    today = now.normalize()
    return today if now.strftime("%H:%M") >= CUTOFF else today - pd.Timedelta(days=1)


def fetch_page(code: str, end) -> pd.DataFrame:
    """end 까지 30거래일. end 는 last_complete_day() 를 넘지 않게 자른다."""
    from src import kis

    end = min(pd.Timestamp(end), last_complete_day())
    body, _ = kis.call(KIS_TR, KIS_PATH, {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": pd.Timestamp(end).strftime("%Y%m%d"),
        "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": "",
    })
    return parse_kis(list(body.get("output2") or []))


def fetch(code: str, start, end=None, max_pages: int = 200) -> pd.DataFrame:
    """start~end 수급. 30거래일씩 거꾸로 받는다. 전 구간이 비면 ValueError."""
    start = pd.Timestamp(start)
    cur = min(pd.Timestamp(end), last_complete_day()) if end is not None else last_complete_day()
    parts = []
    for _ in range(max_pages):
        page = fetch_page(code, cur)
        if page.empty:
            break
        parts.append(page)
        first = page.index.min()
        if first <= start or first >= cur + pd.Timedelta(days=1):
            break
        cur = first - pd.Timedelta(days=1)
    if not parts:
        raise ValueError(f"수급 빈 응답: {code}")
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    out = out[out.index >= start]
    if out.empty:
        raise ValueError(f"수급 빈 응답: {code} ({start:%Y-%m-%d} 이후 없음)")
    return out


def load(code: str) -> pd.DataFrame:
    p = flow_path(code)
    if not p.exists():
        return pd.DataFrame(columns=FLOW_COLS + ["source"])
    return pd.read_csv(p, index_col="Date", parse_dates=True, encoding="utf-8-sig")


def save(code: str, new: pd.DataFrame, overwrite: bool = False, source: str = "kis") -> pd.DataFrame:
    """겹치는 날은 새 값으로 바꾼다(KIS 가 사후 수정한 값 반영)."""
    new = new.copy()
    new["source"] = source
    old = pd.DataFrame() if overwrite else load(code)
    df = pd.concat([old, new]) if len(old) else new
    df = df[~df.index.duplicated(keep="last")].sort_index()
    flow_path(code).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(flow_path(code), index_label="Date", encoding="utf-8-sig")
    return df


def update_start(code: str, default_start) -> pd.Timestamp:
    """증분: 캐시 마지막일 -5일부터 다시 받는다."""
    old = load(code)
    if old.empty:
        return pd.Timestamp(default_start)
    return old.index.max() - pd.Timedelta(days=5)


def flow_panel(codes: list[str], investor: str) -> pd.DataFrame:
    cols = {}
    for c in codes:
        df = load(c)
        if investor in df.columns and len(df):
            cols[c] = df[investor]
    return pd.DataFrame(cols).sort_index()


def normalized_flow(
    flow: pd.DataFrame,
    value: pd.DataFrame,
    lag: int = LAG,
    window: int = NORM_WINDOW,
) -> pd.DataFrame:
    """t 시점에 쓸 수 있는 수급 피처 = flow_{t-lag} / median(value_{t-window..t-1}).

    flow, value 는 같은 거래일 인덱스(휴장일 행 제거된)여야 한다.
    분모도 shift(1): 오늘 거래대금은 장 마감 전엔 모른다.
    """
    idx = value.index.union(flow.index)
    value = value.reindex(idx)
    flow = flow.reindex(index=idx, columns=value.columns)
    denom = value.rolling(window, min_periods=max(5, window // 2)).median().shift(1)
    denom = denom.where(denom > 0)
    return flow.shift(lag) / denom
