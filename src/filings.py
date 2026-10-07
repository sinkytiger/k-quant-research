"""대시보드 공시 피드: DART 공시 목록 + 분류 + 일상 신고 표시 + 공시일·다음 거래일 등락률.

- DART list.json 은 접수 **날짜**만 준다(시각 없음). 그래서 '공시일 등락률'에는 공시 전 움직임이 섞일 수 있다.
  '다음 거래일 등락률'이 공시를 보고 나서 거래할 수 있는 첫날이다 (이벤트 연구의 진입 기준과 같다).
- 등락률은 KRX 일별매매 스냅샷의 등락률(FLUC_RT, 전일 대비, 분할 반영 기준가 대비)을 그대로 쓴다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.data import dart

CATS = [name for name, _ in dart.RULES] + ["기타"]
INSIDER = "임원ㆍ주요주주특정증권등소유상황보고서"
ROUTINE = re.compile(r"일괄신고|파생결합|투자설명서|증권신고서\(채무증권|증권발행실적보고서|" + INSIDER)


def is_routine(title: str) -> bool:
    """매일 쏟아지는 일상 신고 (임원 소유 보고, 증권사 파생결합사채·채무증권 발행 서류)."""
    return bool(ROUTINE.search(str(title)))


def fluc_map(raw: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], float]:
    """KRX 스냅샷 원본 → {(종목코드, 날짜): 등락률(소수)}. 거래 없는 날(거래정지)은 뺀다."""
    if raw.empty:
        return {}
    num = lambda s: pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce")  # noqa: E731
    d = pd.DataFrame({"code": raw["ISU_CD"].astype(str), "date": pd.to_datetime(raw["BAS_DD"].astype(str)),
                      "r": num(raw["FLUC_RT"]) / 100, "vol": num(raw["ACC_TRDVOL"])})
    d = d[(d["vol"] > 0) & d["r"].notna()]
    return {(c, t): float(r) for c, t, r in zip(d["code"], d["date"], d["r"])}


def reaction(code: str, day, calendar: list[pd.Timestamp], fl: dict) -> tuple[float | None, float | None]:
    """(공시일 등락률, 다음 거래일 등락률). 공시일이 휴장이면 공시일 값은 없고 다음 거래일은 그 뒤 첫 개장일."""
    day = pd.Timestamp(day).normalize()
    r0 = fl.get((code, day))
    nxt = next((d for d in calendar if d > day), None)
    r1 = fl.get((code, nxt)) if nxt is not None else None
    return r0, r1


def build(dl: pd.DataFrame, calendar: list[pd.Timestamp], fl: dict, universe: set[str], names: dict[str, str]) -> dict:
    """화면용 압축 행: [날짜, 코드, 이름, 제목, 분류 번호, 일상 신고, 유니버스, 공시일 등락, 다음날 등락, 접수번호]."""
    rows = []
    dl = dl[dl["stock_code"].astype(str).str.len() == 6].sort_values(["rcept_dt", "rcept_no"], ascending=False)
    for r in dl.itertuples():
        code, title = str(r.stock_code), str(r.report_nm).strip()
        r0, r1 = reaction(code, r.rcept_dt, calendar, fl)
        rows.append([f"{r.rcept_dt:%Y-%m-%d}", code, names.get(code, str(r.corp_name)), title,
                     CATS.index(dart.classify(title)), int(is_routine(title)), int(code in universe),
                     None if r0 is None else round(r0, 4), None if r1 is None else round(r1, 4), str(r.rcept_no)])
    return {"cats": CATS, "rows": rows}


def write(path: Path, payload: dict, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("window.KQ_FILINGS=" + json.dumps({**meta, **payload}, ensure_ascii=False, separators=(",", ":")) + ";\n",
                    encoding="utf-8")
