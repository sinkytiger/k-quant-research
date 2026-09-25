"""경로와 .env 로딩. 모든 모듈이 여기서 경로를 가져간다.

모듈들은 `config.PRICES_DIR` 처럼 호출 시점에 속성으로 읽는다.
그래서 테스트에서 monkeypatch 로 경로를 임시 폴더로 바꿀 수 있다.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> None:
    """.env 를 os.environ 에 올린다. 이미 있는 값은 덮지 않는다.

    pykrx 는 import 시점에 KRX_ID/KRX_PW 로 로그인 세션을 만든다.
    그래서 이 함수는 반드시 pykrx import 보다 먼저 불려야 한다(src/krx.py 가 보장).
    """
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            os.environ.setdefault(k.strip(), v)


load_env()

# 데이터 위치. OneDrive 안에 수만 개 CSV 를 두면 동기화가 느려지므로
# .env 의 KQ_DATA_DIR 로 OneDrive 밖(예: C:\KQuantData)을 지정할 수 있다.
DATA = Path(os.environ.get("KQ_DATA_DIR") or ROOT / "data")
LOGS = ROOT / "logs"
OUTPUTS = ROOT / "outputs"

UNIVERSE_DIR = DATA / "universe"
MEMBERSHIP_DIR = UNIVERSE_DIR / "membership"
PRICES_DIR = UNIVERSE_DIR / "prices"
MARKETCAP_DIR = UNIVERSE_DIR / "market_cap"
BENCH_DIR = UNIVERSE_DIR / "bench"
NAMES_CSV = UNIVERSE_DIR / "kospi200_names.csv"
FLOWS_DIR = DATA / "flows"
RAW_DIR = DATA / "raw"

KOSPI200_INDEX = "1028"


def ensure_dirs() -> None:
    for d in (MEMBERSHIP_DIR, PRICES_DIR, MARKETCAP_DIR, BENCH_DIR, FLOWS_DIR, RAW_DIR, LOGS, OUTPUTS):
        d.mkdir(parents=True, exist_ok=True)
