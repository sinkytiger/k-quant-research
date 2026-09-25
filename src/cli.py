"""scripts/ 공용: 콘솔 UTF-8, 로그 파일, 기간 파싱, API 접근 점검."""
from __future__ import annotations

import logging
import re
import sys

import pandas as pd

from src import config


def setup(name: str) -> logging.Logger:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    config.ensure_dirs()
    # 루트는 WARNING: 외부 라이브러리 INFO 로그는 숨기고 이 스크립트 로그만 INFO 로 찍는다
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOGS / f"{name}.log", encoding="utf-8"),
        ],
        force=True,
    )
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.info("DATA=%s", config.DATA)
    return log


def parse_period(s: str, today: pd.Timestamp | None = None) -> pd.Timestamp:
    """'10y', '6mo', '30d' → 시작일."""
    today = today or pd.Timestamp.today().normalize()
    m = re.fullmatch(r"(\d+)\s*(y|mo|d)", s.strip().lower())
    if not m:
        raise ValueError(f"기간 형식 오류: {s} (예: 10y, 6mo, 30d)")
    n, u = int(m.group(1)), m.group(2)
    if u == "y":
        return today - pd.DateOffset(years=n)
    if u == "mo":
        return today - pd.DateOffset(months=n)
    return today - pd.Timedelta(days=n)


# 합병·상폐 종목 중 하나(메리츠화재, 2023 메리츠금융지주에 흡수). 상장 중인 소스에는 없다.
DELISTED_PROBE = ("000060", "2020-01-02", "2020-01-31")


def check_all(log: logging.Logger) -> bool:
    """KRX Open API(시세·시총·유니버스) + KIS(수급). 공식 API 두 개만 쓴다.

    KRX Open API 가 막히면 생존편향 없는 스냅샷을 못 쌓는다. 수집 전에 통과해야 한다.
    """
    a = krx_api_check(log)
    b = kis_check(log)
    log.info("종합: KRX Open API %s / KIS %s", *("OK" if x else "FAIL" for x in (a, b)))
    return a and b


def krx_api_check(log: logging.Logger) -> bool:
    from src import krx_api

    ok = True
    day = pd.Timestamp.today().normalize()
    rows = []
    try:
        for _ in range(10):  # 최근 거래일 찾기 (휴장·미제공이면 빈 응답)
            day -= pd.tseries.offsets.BDay(1)
            rows = krx_api.fetch(krx_api.STOCK_KOSPI, f"{day:%Y%m%d}")
            if rows:
                break
        if rows:
            log.info("[OK] KRX Open API 유가증권 일별매매 %s: %d종목", f"{day:%Y-%m-%d}", len(rows))
        else:
            log.error("[FAIL] KRX Open API 최근 10영업일 모두 빈 응답")
            ok = False
        for api in (krx_api.INDEX_KOSPI, krx_api.ETF):
            n = len(krx_api.fetch(api, f"{day:%Y%m%d}"))
            log.info("[%s] KRX Open API %s: %d행", "OK" if n else "FAIL", api, n)
            ok &= n > 0
        old = krx_api.fetch(krx_api.STOCK_KOSPI, "20200102")
        has = any(r.get("ISU_CD") == DELISTED_PROBE[0] for r in old)
        log.info("[%s] 2020-01-02 스냅샷에 상폐 종목 %s 포함: %s", "OK" if has else "FAIL", DELISTED_PROBE[0], has)
        ok &= has
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] KRX Open API: %s", e)
        ok = False
    return ok


def kis_check(log: logging.Logger) -> bool:
    """KIS 날짜 지정 수급: 최근과 2020년 상폐 종목 둘 다 나와야 10년 백필이 된다."""
    from src import kis
    from src.data import flows

    ok = True
    try:
        df = flows.fetch_page("005930", pd.Timestamp.today().normalize())
        log.info("[OK] KIS(%s) 종목별 투자자 005930: %d일 (~%s)", kis.env(), len(df), f"{df.index.max():%Y-%m-%d}")
        ok &= len(df) > 0
        code, _, b = DELISTED_PROBE
        old = flows.fetch_page(code, b)
        log.info("[%s] KIS 과거 수급: 상폐 종목 %s %s 기준 %d일", "OK" if len(old) else "FAIL", code, b, len(old))
        ok &= len(old) > 0
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] KIS: %s", e)
        ok = False
    return ok
