"""DART 공시 목록 수집 (OpenDART list.json) + 공시 분류.

- 회사 지정 없이 조회하면 기간은 3개월 이하 → 월 단위로 끊고 페이지(100건)를 넘겨 받는다.
- corp_cls=Y (유가증권)만. 유니버스가 코스피 시총 상위 200 이라서.
- list.json 은 접수 **날짜**만 주고 시각은 없다 → 이벤트 연구에서는 장 마감 후 공시일 수 있다고 보고
  접수일 다음 거래일 종가를 진입 시점으로 쓴다(보수적).
- 저장: raw/dart/list/<YYYYMM>.csv. 한도: 하루 20,000건.
- 분류 규칙: 데이터크롤링 기획서(PIPELINE_PLAN.md) 2-4장 12개, 위에서부터 먼저 걸리는 쪽.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import requests

from src import config

URL = "https://opendart.fss.or.kr/api/list.json"
COLS = ["rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code", "corp_cls", "report_nm", "flr_nm", "rm"]
MIN_INTERVAL = 0.1
_last = 0.0


class DartError(RuntimeError):
    pass


def list_dir() -> Path:
    return config.DATA / "raw" / "dart" / "list"


def month_path(ym: str) -> Path:
    return list_dir() / f"{ym}.csv"


def _get(params: dict) -> dict:
    """실제 HTTP. 테스트는 이 함수를 바꿔 끼운다."""
    r = requests.get(URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _call(params: dict, retries: int = 3) -> dict:
    global _last
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise DartError(".env 에 DART_API_KEY 가 없다")
    err = None
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            j = _get({"crtfc_key": key, **params})
        except (requests.RequestException, ValueError) as e:
            err = e
            time.sleep(2.0 * (attempt + 1))
            continue
        st = str(j.get("status"))
        if st in ("000", "013"):  # 정상 / 데이터 없음
            return j
        if st == "020":  # 요청 제한 초과
            raise DartError(f"DART 호출 한도 초과: {j.get('message')}")
        err = DartError(f"DART {st}: {j.get('message')}")
        time.sleep(2.0 * (attempt + 1))
    raise DartError(str(err))


def fetch_range(bgn: str, end: str, corp_cls: str = "Y") -> pd.DataFrame:
    """bgn~end (YYYYMMDD, 3개월 이하) 전 페이지."""
    rows, page = [], 1
    while True:
        j = _call({"bgn_de": bgn, "end_de": end, "corp_cls": corp_cls, "page_no": page, "page_count": 100})
        rows += j.get("list") or []
        total = int(j.get("total_page") or 0)
        if page >= total:
            break
        page += 1
    df = pd.DataFrame(rows)
    for c in COLS:
        if c not in df.columns:
            df[c] = ""
    return df[COLS].drop_duplicates("rcept_no")


def fetch_month(ym: str, corp_cls: str = "Y") -> pd.DataFrame:
    start = pd.Timestamp(f"{ym[:4]}-{ym[4:]}-01")
    end = min(start + pd.offsets.MonthEnd(0), pd.Timestamp.today().normalize())
    return fetch_range(f"{start:%Y%m%d}", f"{end:%Y%m%d}", corp_cls)


def save_month(ym: str, df: pd.DataFrame) -> Path:
    p = month_path(ym)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def load_all(start=None, end=None) -> pd.DataFrame:
    parts = []
    for p in sorted(list_dir().glob("*.csv")) if list_dir().exists() else []:
        parts.append(pd.read_csv(p, dtype=str, encoding="utf-8-sig").fillna(""))
    if not parts:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(parts, ignore_index=True).drop_duplicates("rcept_no")
    df["rcept_dt"] = pd.to_datetime(df["rcept_dt"], format="%Y%m%d")
    if start is not None:
        df = df[df["rcept_dt"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["rcept_dt"] <= pd.Timestamp(end)]
    return df.sort_values(["rcept_dt", "rcept_no"]).reset_index(drop=True)


# ---------------- 분류 (PIPELINE_PLAN.md 2-4장, 위에서부터 먼저 걸리는 쪽) ----------------
RULES: list[tuple[str, callable]] = [
    ("정정공시", lambda s: s.startswith("[")),
    ("주요사항", lambda s: s.startswith("주요사항보고서")),
    ("자사주", lambda s: "자기주식" in s),
    ("자금조달", lambda s: "증권발행실적보고서" not in s and any(k in s for k in ("유상증자", "사채", "증권발행결과", "권리락", "발행가액"))),
    ("리스크", lambda s: any(k in s for k in ("소송", "불성실공시", "거래정지", "회생", "횡령", "배임", "상장폐지", "관리종목", "감사의견"))),
    ("지배구조", lambda s: any(k in s for k in ("임원ㆍ주요주주", "대량보유", "최대주주"))),
    ("수주", lambda s: any(k in s for k in ("단일판매ㆍ공급계약", "유동성공급계약"))),
    ("투자", lambda s: any(k in s for k in ("신규시설투자", "유형자산취득", "타법인주식및출자증권취득"))),
    ("M&A", lambda s: any(k in s for k in ("합병", "분할", "영업양수", "영업양도", "주식교환"))),
    ("정기보고", lambda s: s.startswith(("사업보고서", "반기보고서", "분기보고서"))),
    ("감사", lambda s: "감사보고서" in s),
]


def classify(report_nm: str) -> str:
    s = str(report_nm).strip()
    for name, rule in RULES:
        if rule(s):
            return name
    return "기타"
