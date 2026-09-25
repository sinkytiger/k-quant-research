"""한국투자증권(KIS) Open API 조회 클라이언트. 시세·수급 조회 전용(주문 없음).

- 토큰: POST /oauth2/tokenP. 유효 24시간, 발급은 1분에 1회 → data/.kis_token.json 에 캐시.
- 공통 헤더: authorization, appkey, appsecret, tr_id, custtype=P.
- 성공 판정: body rt_cd == "0". 실전 초당 20건 → 호출 간 0.06초 이상.
- KIS_ENV=prod(실전) / vts(모의). 이 파이프라인은 조회만 하므로 prod 를 쓴다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import requests

from src import config

DOMAINS = {
    "prod": "https://openapi.koreainvestment.com:9443",
    "vts": "https://openapivts.koreainvestment.com:29443",
}
MIN_INTERVAL = {"prod": 0.06, "vts": 0.55}

_last = 0.0


class KisError(RuntimeError):
    pass


def env() -> str:
    e = (os.environ.get("KIS_ENV") or "prod").lower()
    if e not in DOMAINS:
        raise KisError(f"KIS_ENV={e} — prod 또는 vts 여야 한다")
    return e


def base_url() -> str:
    return DOMAINS[env()]


def _token_path():
    return config.DATA / f".kis_token_{env()}.json"


def _post(url: str, body: dict) -> dict:
    r = requests.post(url, json=body, timeout=30)
    return r.json()


def _get(url: str, headers: dict, params: dict) -> tuple[dict, dict]:
    r = requests.get(url, headers=headers, params=params, timeout=30)
    return r.json(), dict(r.headers)


def _keys() -> tuple[str, str]:
    k, s = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not k or not s:
        raise KisError(".env 에 KIS_APP_KEY / KIS_APP_SECRET 이 없다")
    return k, s


def token(now: datetime | None = None) -> str:
    """캐시된 토큰이 1시간 이상 남았으면 재사용. 아니면 새로 발급."""
    now = now or datetime.now()
    p = _token_path()
    if p.exists():
        c = json.loads(p.read_text(encoding="utf-8"))
        exp = datetime.strptime(c["expired"], "%Y-%m-%d %H:%M:%S")
        if exp - now > timedelta(hours=1):
            return c["access_token"]
    k, s = _keys()
    j = _post(f"{base_url()}/oauth2/tokenP",
              {"grant_type": "client_credentials", "appkey": k, "appsecret": s})
    tok = j.get("access_token")
    if not tok:
        raise KisError(f"KIS 토큰 발급 실패: {j.get('error_description') or j.get('msg1') or j}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"access_token": tok, "expired": j["access_token_token_expired"]}),
                 encoding="utf-8")
    return tok


def call(tr_id: str, path: str, params: dict, tr_cont: str = "") -> tuple[dict, dict]:
    global _last
    k, s = _keys()
    headers = {
        "authorization": f"Bearer {token()}",
        "appkey": k,
        "appsecret": s,
        "tr_id": tr_id,
        "custtype": "P",
        "tr_cont": tr_cont,
        "content-type": "application/json; charset=utf-8",
    }
    err = None
    for attempt in range(3):
        wait = MIN_INTERVAL[env()] - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            body, h = _get(f"{base_url()}{path}", headers, params)
        except (requests.RequestException, ValueError) as e:
            err = e
            time.sleep(1.0 * (attempt + 1))
            continue
        if str(body.get("rt_cd")) == "0":
            return body, h
        err = KisError(f"{tr_id}: {body.get('msg_cd')} {body.get('msg1')}")
        if "초당" in str(body.get("msg1", "")):  # 호출 제한 초과 → 잠시 쉬고 재시도
            time.sleep(1.0)
            continue
        break
    raise KisError(str(err))


def investor_daily(code: str) -> list[dict]:
    """종목별 투자자 (FHKST01010900). 최근 30거래일만 준다. 금액 단위 백만원."""
    body, _ = call("FHKST01010900", "/uapi/domestic-stock/v1/quotations/inquire-investor",
                   {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    return list(body.get("output") or [])
