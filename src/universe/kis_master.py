"""KIS 종목마스터 (인증 불필요 공식 다운로드) → **현재** 지수 편입 여부·업종·종목명.

- kospi_code.mst.zip / kosdaq_code.mst.zip, cp949 고정폭.
- 행 = [단축코드 9][표준코드 12][한글명 ...][뒷부분 고정폭]. 뒷부분 길이: 코스피 227, 코스닥 221
  (KIS 공식 파서는 줄바꿈 포함 228/222 로 자른다).
- 코스피 뒷부분: 그룹코드2 시총규모1 업종대4 중4 소4 제조업1 저유동성1 지배구조1
  KOSPI200섹터1(0=비편입) KOSPI100 1 KOSPI50 1 ...
- 코스닥 뒷부분: 그룹코드2 시총규모1 업종대4 중4 소4 + 1자리 플래그 20개 뒤 KOSDAQ150(Y/N).
- 2026-09-25 확인: KOSPI200 표시 201종목, KOSDAQ150 150종목.

과거 시점 구성은 알 수 없다(파일은 항상 오늘 기준). 백테스트에는 membership.py 를 쓴다.
원본 파서: KIS 공식 저장소 stocks_info/kis_kospi_code_mst.py, kis_kosdaq_code_mst.py
"""
from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from src import config

URL = "https://new.real.download.dws.co.kr/common/master/{mkt}_code.mst.zip"
TAIL = {"kospi": 227, "kosdaq": 221}


def cache_path(mkt: str, day: date | None = None) -> Path:
    day = day or date.today()
    return config.DATA / "raw" / "kis_master" / f"{day:%Y%m%d}_{mkt}_code.mst"


def download(mkt: str, force: bool = False) -> str:
    """오늘 파일이 있으면 재사용. 텍스트(utf-8 로 다시 저장)를 돌려준다."""
    p = cache_path(mkt)
    if p.exists() and not force:
        return p.read_text(encoding="utf-8")
    import requests

    r = requests.get(URL.format(mkt=mkt), timeout=60)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    text = z.read(z.namelist()[0]).decode("cp949")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return text


def parse(text: str, mkt: str) -> pd.DataFrame:
    tail = TAIL[mkt]
    rows = []
    for line in text.splitlines():
        if not line.strip() or len(line) <= tail + 21:
            continue
        head, back = line[:-tail], line[-tail:]
        row = {
            "code": head[0:9].strip(),
            "std_code": head[9:21].strip(),
            "name": head[21:].strip(),
            "group": back[0:2],
            "sector_l": back[3:7],
            "sector_m": back[7:11],
            "sector_s": back[11:15],
        }
        if mkt == "kospi":
            row["kospi200_sector"] = back[18]
            row["kospi200"] = back[18] not in (" ", "0")
            row["kospi100"] = back[19] == "Y"
            row["kospi50"] = back[20] == "Y"
        else:
            row["kosdaq150"] = back[35] == "Y"
        rows.append(row)
    df = pd.DataFrame(rows)
    df["market"] = mkt.upper()
    return df


def load(mkt: str, force: bool = False) -> pd.DataFrame:
    return parse(download(mkt, force=force), mkt)


def current_members(index: str) -> list[str]:
    """index: 'KOSPI200' | 'KOSDAQ150' | 'KOSPI100' | 'KOSPI50'. 오늘 기준 편입 종목."""
    col = index.lower()
    mkt = "kosdaq" if col == "kosdaq150" else "kospi"
    df = load(mkt)
    return sorted(df.loc[df[col], "code"])
