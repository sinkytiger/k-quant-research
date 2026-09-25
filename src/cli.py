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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOGS / f"{name}.log", encoding="utf-8"),
        ],
        force=True,
    )
    log = logging.getLogger(name)
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


# 합병·상폐 종목 중 하나(메리츠화재, 2023 메리츠금융지주에 흡수). KRX 로만 과거가 나온다.
DELISTED_PROBE = ("000060", "2020-01-02", "2020-01-31")


def krx_check(log: logging.Logger) -> bool:
    """KRX 가 막혀 있으면 생존편향이 남은 데이터가 쌓인다. 수집 전에 반드시 통과해야 한다."""
    from src.universe import kospi200, prices

    ok = True
    if krx.has_credentials():
        log.info("[OK] .env 에 KRX_ID/KRX_PW 있음")
    else:
        log.warning("[WARN] KRX_ID/KRX_PW 없음. 익명 요청은 빈 응답으로 막힐 수 있다.")

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

    try:
        end = pd.Timestamp.today().normalize()
        df = prices.fetch_krx("005930", (end - pd.Timedelta(days=14)).strftime("%Y-%m-%d"))
        log.info("[OK] KRX 시세 005930 %d행 (마지막 %s)", len(df), f"{df.index.max():%Y-%m-%d}")
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] KRX 시세 조회 실패: %s", e)
        ok = False

    code, a, b = DELISTED_PROBE
    try:
        df = prices.fetch_krx(code, a, b)
        log.info("[OK] 상폐 종목 %s 과거 시세 %d행 — 생존편향 메우기 가능", code, len(df))
    except Exception as e:  # noqa: BLE001
        log.error("[FAIL] 상폐 종목 %s 과거 시세 실패: %s", code, e)
        ok = False

    log.info("KRX 점검 결과: %s", "통과" if ok else "실패 — 수집을 진행하지 말 것")
    return ok
