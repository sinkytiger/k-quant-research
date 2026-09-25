"""scripts/ 공용: 콘솔 UTF-8, 로그 파일, 기간 파싱, KRX 접근 점검."""
from __future__ import annotations

import logging
import re
import sys

import pandas as pd

from src import config, krx


def setup(name: str) -> logging.Logger:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    config.ensure_dirs()
    # 루트는 WARNING: pykrx 가 루트에 logging.info(args, kwargs) 를 잘못 찍어 트레이스백을 쏟아낸다
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
    """KRX Open API(시세·시총) + KIS(수급 증분) + pykrx(구성종목·과거 수급).

    KRX Open API 나 pykrx 가 막히면 생존편향이 남은 데이터가 쌓인다. 수집 전에 통과해야 한다.
    """
    a = krx_api_check(log)
    b = kis_check(log)
    c = krx_check(log)
    log.info("종합: KRX Open API %s / KIS %s / pykrx %s", *("OK" if x else "FAIL" for x in (a, b, c)))
    return a and b and c


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
    from src import kis

    try:
        rows = kis.investor_daily("005930")
        log.info("[OK] KIS(%s) 종목별 투자자 005930: %d일 (%s~%s)", kis.env(), len(rows),
                 rows[-1]["stck_bsop_date"], rows[0]["stck_bsop_date"])
        return len(rows) > 0
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] KIS: %s", e)
        return False


def krx_check(log: logging.Logger) -> bool:
    """pykrx(KRX 웹 로그인): KOSPI200 과거 구성종목과 10년 수급 백필에만 쓴다."""
    from src.universe import kospi200

    ok = True
    if krx.has_credentials():
        log.info("[OK] .env 에 KRX_ID/KRX_PW 있음")
    else:
        log.warning("[FAIL] KRX_ID/KRX_PW 없음 — pykrx 는 로그인 없이는 빈 응답이다 (data.krx.co.kr 계정)")
        return False
    code, msg = krx.login_probe()
    if code not in krx.LOGIN_OK:
        log.error("[FAIL] KRX 로그인 %s: %s — 더 시도하지 않는다. data.krx.co.kr 에서 확인 후 .env 수정", code, msg)
        return False
    log.info("[OK] KRX 로그인 확인 (%s)", code)

    try:
        asof = pd.Timestamp.today().normalize().replace(day=1)
        codes = kospi200.fetch_members(asof)
        if len(codes) >= 190:
            log.info("[OK] KOSPI200 구성종목 %d개 (%s)", len(codes), f"{asof:%Y-%m-%d}")
        else:
            log.error("[FAIL] KOSPI200 구성종목 %d개 — 접근 막힘 추정", len(codes))
            ok = False
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] 구성종목 조회 예외: %s", e)
        ok = False

    from src.data import flows

    code, a, b = DELISTED_PROBE
    try:
        df = flows.fetch(code, a, b)
        log.info("[OK] pykrx 수급: 상폐 종목 %s 2020-01 %d행 — 과거 수급 백필 가능", code, len(df))
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] pykrx 수급 조회 실패: %s", e)
        ok = False

    log.info("pykrx 점검 결과: %s", "통과" if ok else "실패")
    return ok
