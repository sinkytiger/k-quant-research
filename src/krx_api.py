"""KRX Open API (data-dbg.krx.co.kr) 클라이언트.

- 헤더 AUTH_KEY. 반드시 https (http 는 리다이렉트되며 헤더가 사라진다).
- 응답은 항상 HTTP 200. 실패는 body respCode (401 = 키 오류 또는 서비스 미신청).
- 데이터는 OutBlock_1. 휴장일이면 빈 배열.
- 한도: 인증키당 하루 10,000건 → 호출 수를 파일에 세고 DAILY_BUDGET 에서 멈춘다.
- D일 데이터는 D+1 오전 8시 전후에 나온다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date

import requests

from src import config

BASE = "https://data-dbg.krx.co.kr/svc/apis"
DAILY_BUDGET = 9_500  # 10,000 한도에서 여유분
MIN_INTERVAL = 0.1

STOCK_KOSPI = "sto/stk_bydd_trd"
STOCK_KOSDAQ = "sto/ksq_bydd_trd"
INDEX_KOSPI = "idx/kospi_dd_trd"
INDEX_KOSDAQ = "idx/kosdaq_dd_trd"
ETF = "etp/etf_bydd_trd"

_last = 0.0


class KrxApiError(RuntimeError):
    pass


class QuotaExceeded(KrxApiError):
    pass


def _quota_path():
    return config.DATA / "raw" / "krx_api_quota.json"


def used_today() -> int:
    p = _quota_path()
    if not p.exists():
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))
    return int(d.get(date.today().isoformat(), 0))


def _count_call() -> None:
    p = _quota_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    d = {today: int(d.get(today, 0)) + 1}  # 오늘 것만 남긴다
    p.write_text(json.dumps(d), encoding="utf-8")


def _get(url: str, params: dict, headers: dict) -> dict:
    """실제 HTTP 호출. 테스트는 이 함수를 바꿔 끼운다."""
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch(api_id: str, bas_dd: str, retries: int = 3) -> list[dict]:
    """하루치 OutBlock_1. 휴장일이면 []. 키/서비스 문제면 KrxApiError."""
    global _last
    key = os.environ.get("KRX_API_KEY")
    if not key:
        raise KrxApiError(".env 에 KRX_API_KEY 가 없다")
    if used_today() >= DAILY_BUDGET:
        raise QuotaExceeded(f"오늘 KRX API 호출 {used_today()}건 — 한도 보호로 중단. 내일 이어서 실행")
    err = None
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            _count_call()
            j = _get(f"{BASE}/{api_id}.json", {"basDd": bas_dd}, {"AUTH_KEY": key})
        except (requests.RequestException, ValueError) as e:
            err = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if "OutBlock_1" in j:
            return list(j["OutBlock_1"] or [])
        code = str(j.get("respCode", ""))
        msg = j.get("respMsg", j)
        if code == "401":
            raise KrxApiError(f"{api_id}: 401 — 인증키 오류 또는 이 서비스 이용신청 누락 ({msg})")
        err = KrxApiError(f"{api_id} {bas_dd}: {msg}")
        time.sleep(1.5 * (attempt + 1))
    raise KrxApiError(f"{api_id} {bas_dd} 실패: {err}")


def to_num(s):
    """'1,234' '-' '' → float/NaN."""
    if s is None:
        return float("nan")
    s = str(s).replace(",", "").strip()
    if s in ("", "-"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")
