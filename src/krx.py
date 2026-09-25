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


def stock():
    global _backend
    if _backend is None:
        config.load_env()
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
