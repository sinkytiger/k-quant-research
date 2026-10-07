"""DART 재무 주요계정 (OpenDART 다중회사 주요계정 fnlttMultiAcnt, 한 번에 100개 회사).

- 회사 고유번호: corpCode.xml (zip) → raw/dart/corpcode.csv (종목코드 있는 회사만). 30일마다 새로 받는다.
- 보고서: 1분기 11013, 반기 11012, 3분기 11014, 사업보고서 11011.
  손익(IS)은 thstrm_amount = 그 분기 3개월(사업보고서는 연간), thstrm_add_amount = 연초부터 누적(분기·반기 보고서).
  → 누적값으로 통일해 저장하고 분기 값은 누적 차이로 만든다 (src/valuation.py).
- 저장: raw/dart/fin/<연도>_<보고서>.csv (연결 CFS·별도 OFS 둘 다, 화면은 연결 우선).
- rcept_no 앞 8자리 = 접수일 → 연구에서 공시 전 숫자를 쓰지 않도록(시점 정합) 같이 저장한다.
"""
from __future__ import annotations

import io
import os
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from src import config

BASE = "https://opendart.fss.or.kr/api"
REPORTS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}  # 보고서 → 분기 번호
ACCOUNTS = {"매출액": "revenue", "영업이익": "op", "당기순이익(손실)": "ni", "당기순이익": "ni",
            "자산총계": "assets", "부채총계": "liab", "자본총계": "equity"}
MIN_INTERVAL = 0.15
_last = 0.0


class DartFinError(RuntimeError):
    pass


def fin_dir() -> Path:
    return config.DATA / "raw" / "dart" / "fin"


def corpcode_path() -> Path:
    return config.DATA / "raw" / "dart" / "corpcode.csv"


def _num(s):
    s = str(s or "").replace(",", "").strip()
    try:
        return float(s) if s not in ("", "-") else float("nan")
    except ValueError:
        return float("nan")


def _get(path: str, params: dict, binary: bool = False, retries: int = 3):
    global _last
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise DartFinError(".env 에 DART_API_KEY 가 없다")
    err = None
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            r = requests.get(f"{BASE}/{path}", params={"crtfc_key": key, **params}, timeout=60)
            r.raise_for_status()
            if binary:
                return r.content
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            err = e
            time.sleep(2.0 * (attempt + 1))
            continue
        st = str(j.get("status"))
        if st in ("000", "013"):
            return j
        if st == "020":
            raise DartFinError(f"DART 호출 한도 초과: {j.get('message')}")
        err = DartFinError(f"DART {st}: {j.get('message')}")
        time.sleep(2.0 * (attempt + 1))
    raise DartFinError(str(err))


def parse_corpcode(xml: str) -> pd.DataFrame:
    """CORPCODE.xml → 종목코드 있는 회사. <list> 항목 단위로 읽는다.

    (예전에는 정규식으로 이어 읽어서, 영문이 섞인 새 종목코드(예: 0041L0)를 만나면 다음 회사의 코드를 끌어오는 버그가 있었다.)
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    rows = [{"corp_code": (e.findtext("corp_code") or "").strip(), "corp_name": (e.findtext("corp_name") or "").strip(),
             "stock_code": (e.findtext("stock_code") or "").strip()} for e in root.iter("list")]
    df = pd.DataFrame(rows, columns=["corp_code", "corp_name", "stock_code"])
    return df[df["stock_code"].str.fullmatch(r"[0-9A-Z]{6}")].drop_duplicates("stock_code").reset_index(drop=True)


def corp_codes(max_age_days: int = 30) -> pd.DataFrame:
    p = corpcode_path()
    if p.exists() and (time.time() - p.stat().st_mtime) < max_age_days * 86400:
        return pd.read_csv(p, dtype=str)
    z = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml", {}, binary=True)))
    df = parse_corpcode(z.read(z.namelist()[0]).decode("utf-8"))
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return df


def parse_multi(items: list[dict], reprt: str) -> pd.DataFrame:
    """응답 list → (corp_code, stock_code, rcept_no, fs, item, value). 손익은 연초부터 누적값으로."""
    out = []
    for it in items:
        item = ACCOUNTS.get(str(it.get("account_nm", "")).strip())
        if not item:
            continue
        sj = it.get("sj_div")
        if sj == "IS" and reprt in ("11012", "11014"):
            v = _num(it.get("thstrm_add_amount"))
            if v != v:  # 누적이 비어 있으면 당분기 값(반기 보고서에서 누적만 주는 회사)
                v = _num(it.get("thstrm_amount"))
        else:
            v = _num(it.get("thstrm_amount"))
        out.append({"corp_code": it.get("corp_code"), "stock_code": str(it.get("stock_code") or "").strip(),
                    "rcept_no": it.get("rcept_no"), "fs": it.get("fs_div"), "item": item, "value": v})
    df = pd.DataFrame(out, columns=["corp_code", "stock_code", "rcept_no", "fs", "item", "value"])
    return df.drop_duplicates(["corp_code", "fs", "item"], keep="first")  # 당기순이익 줄이 둘 오면 첫 줄


def fetch(year: int, reprt: str, corps: list[str], chunk: int = 100, skipped: list | None = None) -> pd.DataFrame:
    """100개씩 묶어 받는다. 묶음이 계속 실패하면 반으로 쪼개 다시, 회사 하나까지 실패하면 그 회사만 건너뛴다(skipped)."""
    parts = []

    def one(group):
        try:
            j = _get("fnlttMultiAcnt.json", {"corp_code": ",".join(group), "bsns_year": str(year), "reprt_code": reprt}, retries=2)
            parts.append(parse_multi(list(j.get("list") or []), reprt))
        except DartFinError as e:
            if "한도" in str(e):
                raise
            if len(group) == 1:
                if skipped is not None:
                    skipped.append(group[0])
                return
            half = len(group) // 2
            one(group[:half])
            one(group[half:])

    for i in range(0, len(corps), chunk):
        one(corps[i:i + chunk])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["corp_code", "stock_code", "rcept_no", "fs", "item", "value"])


def path(year: int, reprt: str) -> Path:
    return fin_dir() / f"{year}_{reprt}.csv"


def save(year: int, reprt: str, df: pd.DataFrame) -> None:
    p = path(year, reprt)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")


def load_all() -> pd.DataFrame:
    """모든 보고서 → long 표 (year, q, corp_code, stock_code, rcept_no, rcept_dt, fs, item, value)."""
    parts = []
    for p in sorted(fin_dir().glob("*.csv")) if fin_dir().exists() else []:
        y, r = p.stem.split("_")
        d = pd.read_csv(p, dtype={"corp_code": str, "stock_code": str, "rcept_no": str}, encoding="utf-8-sig")
        if d.empty:
            continue
        d["year"], d["q"] = int(y), REPORTS[r]
        parts.append(d)
    if not parts:
        return pd.DataFrame(columns=["year", "q", "corp_code", "stock_code", "rcept_no", "rcept_dt", "fs", "item", "value"])
    df = pd.concat(parts, ignore_index=True)
    df["stock_code"] = df["stock_code"].str.zfill(6)
    df["rcept_dt"] = pd.to_datetime(df["rcept_no"].str[:8], format="%Y%m%d", errors="coerce")
    return df
