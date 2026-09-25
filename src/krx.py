"""pykrx 접근 단일 창구.

- load_env() 가 끝난 뒤에만 pykrx 를 import 한다(로그인 세션 생성 시점 문제).
- 다른 모듈은 pykrx 를 직접 import 하지 않는다. 반드시 krx.stock() 을 쓴다.
- 테스트는 set_backend() 로 가짜 객체를 주입한다.
"""
from __future__ import annotations

import time

from src import config

_backend = None
_last_call = 0.0
MIN_INTERVAL = 0.3  # KRX 연속 호출 간격(초). 너무 빠르면 빈 응답으로 막힌다.


class KrxLoginError(RuntimeError):
    pass


_LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
_LOGIN_JSP = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
_LOGIN_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_login_checked: tuple[str, str] | None = None

# CD001 정상, CD011 중복 로그인(다른 곳에서 접속 중 — pykrx 가 그쪽을 끊고 들어간다)
LOGIN_OK = {"CD001", "CD011"}


def login_probe() -> tuple[str, str]:
    """pykrx 를 import 하기 전에 로그인을 딱 한 번 확인한다. (코드, 메시지)

    pykrx 는 import 할 때와 호출이 실패할 때마다 재로그인을 시도한다. 비밀번호가 틀리면
    몇 번 만에 계정이 잠긴다(CD007 '패스워드 오류수에 의한 잠금'). 그래서 먼저 여기서 본다.
    중복 로그인 강제(skipDup)는 보내지 않는다 — 다른 곳의 세션을 끊지 않기 위해.
    """
    global _login_checked
    if _login_checked is not None:
        return _login_checked
    import os

    import requests

    config.load_env()
    if not has_credentials():
        _login_checked = ("NOCRED", ".env 에 KRX_ID/KRX_PW 없음")
        return _login_checked
    s = requests.Session()
    s.get(_LOGIN_PAGE, headers={"User-Agent": _UA}, timeout=15)
    s.get(_LOGIN_JSP, headers={"User-Agent": _UA, "Referer": _LOGIN_PAGE}, timeout=15)
    r = s.post(_LOGIN_URL, data={"mbrNm": "", "telNo": "", "di": "", "certType": "",
                                 "mbrId": os.environ["KRX_ID"], "pw": os.environ["KRX_PW"]},
               headers={"User-Agent": _UA, "Referer": _LOGIN_PAGE}, timeout=15)
    try:
        d = r.json()
        _login_checked = (str(d.get("_error_code", "")), str(d.get("_error_message", "")))
    except ValueError:
        _login_checked = (f"HTTP{r.status_code}", "JSON 아님")
    return _login_checked


def stock():
    global _backend
    if _backend is None:
        code, msg = login_probe()
        if code not in LOGIN_OK:
            raise KrxLoginError(f"KRX 로그인 실패 {code}: {msg} — pykrx 를 쓰지 않는다 (재시도하면 계정이 잠긴다)")
        from pykrx import stock as _stock  # 반드시 load_env 이후

        _backend = _stock
    return _backend


def set_backend(obj) -> None:
    global _backend
    _backend = obj


def throttle() -> None:
    """실제 pykrx 를 쓸 때만 호출 간격을 둔다(가짜 백엔드 테스트는 즉시 통과)."""
    global _last_call
    if _backend is not None and getattr(_backend, "__name__", "").startswith("pykrx"):
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def is_empty(x) -> bool:
    """pykrx 는 오류 시 리스트 대신 빈 DataFrame 을 준다. `if not x` 는 터지므로 len 으로 본다."""
    return x is None or len(x) == 0


def has_credentials() -> bool:
    import os

    return bool(os.environ.get("KRX_ID")) and bool(os.environ.get("KRX_PW"))
