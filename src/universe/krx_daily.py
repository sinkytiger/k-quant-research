"""KRX Open API 일별 스냅샷 → 종목별 수정주가 / 시가총액 / 벤치마크.

하루 1회 호출로 그날 시장 전 종목이 나온다. **그날 상장돼 있던 종목 전부**라서
나중에 상폐·합병된 종목도 들어 있다(시세 쪽 생존편향이 원천적으로 없다).

스냅샷: raw/krx/<dataset>/<YYYYMMDD>.csv  (원 응답 그대로, 문자열)
휴장일: raw/krx/holidays.txt (빈 응답이었던 날, 다시 호출하지 않는다)

수정주가: KRX 가격은 수정 전 원시값이지만 CMPPREVDD_PRC(대비)는 수정 기준가 대비다.
  예) 삼성전자 2018-05-04 50:1 분할: 종가 51,900, 대비 −1,100 → 기준가 53,000 = 2,650,000/50
  그래서 f_t = (종가_t − 대비_t) / 종가_{t−1} 이 그날의 조정계수이고,
  수정가_t = 원시가_t × Π_{s>t} f_s. 거래량은 반대로 나눈다.
  현금배당은 KRX 기준가에 반영되지 않으므로 가격수익률(배당 제외)이다. pykrx adjusted 와 같은 기준.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config, krx_api

DATASETS = {
    "stk": krx_api.STOCK_KOSPI,
    "ksq": krx_api.STOCK_KOSDAQ,
    "idx_kospi": krx_api.INDEX_KOSPI,
    "etf": krx_api.ETF,
}
# 벤치마크: (파일명, 데이터셋, 식별 열, 값)
BENCH_SOURCES = {
    "069500": ("etf", "ISU_CD", "069500"),
    "KOSPI": ("idx_kospi", "IDX_NM", "코스피"),
    "KOSPI200": ("idx_kospi", "IDX_NM", "코스피200"),
}
ETF_KEEP = {"069500", "102110", "229200", "232080"}  # ETF 는 벤치 후보만 저장(용량)


def root() -> Path:
    return config.DATA / "raw" / "krx"


def snap_path(ds: str, day) -> Path:
    return root() / ds / f"{pd.Timestamp(day):%Y%m%d}.csv"


def holidays_path() -> Path:
    return root() / "holidays.txt"


def load_holidays() -> set[pd.Timestamp]:
    p = holidays_path()
    if not p.exists():
        return set()
    return {pd.Timestamp(x) for x in p.read_text(encoding="utf-8").split() if x.strip()}


def add_holiday(day) -> None:
    p = holidays_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{pd.Timestamp(day):%Y%m%d}\n")


def saved_days(ds: str) -> list[pd.Timestamp]:
    d = root() / ds
    if not d.exists():
        return []
    return sorted(pd.Timestamp(p.stem) for p in d.glob("*.csv"))


def candidate_days(start, end) -> list[pd.Timestamp]:
    """평일 중 휴장일로 확인된 날을 뺀 날들."""
    hol = load_holidays()
    return [d for d in pd.bdate_range(start, end) if d not in hol]


CLOSE_FIELD = {"stk": "TDD_CLSPRC", "ksq": "TDD_CLSPRC", "etf": "TDD_CLSPRC", "idx_kospi": "CLSPRC_IDX"}
CALENDAR_DS = "stk"  # 휴장일 판정은 유가증권 일별매매로만 한다


def has_prices(ds: str, rows: list[dict]) -> bool:
    """ETF API 는 휴장일에 빈 배열 대신 가격 칸이 빈 행을 준다. 종가가 하나라도 있어야 거래일."""
    f = CLOSE_FIELD[ds]
    return any(str(r.get(f, "")).replace(",", "").strip() not in ("", "-", "0") for r in rows)


def collect_day(ds: str, day) -> str:
    """'saved' | 'holiday' | 'missing' | 'exists'. 한도 초과는 QuotaExceeded 로 올라간다.

    휴장일 기록은 CALENDAR_DS 가 비었을 때만 한다. 다른 데이터셋이 비면 'missing' 으로 두고
    다음 실행에서 다시 받는다(자정 무렵 일시적 빈 응답을 휴장으로 굳히지 않기 위해).
    """
    day = pd.Timestamp(day)
    p = snap_path(ds, day)
    if p.exists():
        return "exists"
    rows = krx_api.fetch(DATASETS[ds], f"{day:%Y%m%d}")
    if not has_prices(ds, rows):
        if ds != CALENDAR_DS:
            return "missing"
        # 오늘·어제는 아직 안 나온 것일 수 있으니 휴장으로 적지 않는다
        if (pd.Timestamp.today().normalize() - day).days > 2 and day not in load_holidays():
            add_holiday(day)
        return "holiday"
    df = pd.DataFrame(rows)
    if ds == "etf":
        df = df[df["ISU_CD"].isin(ETF_KEEP)]
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")
    return "saved"


def load_snapshots(ds: str, start=None, end=None) -> pd.DataFrame:
    parts = []
    for d in saved_days(ds):
        if (start is not None and d < pd.Timestamp(start)) or (end is not None and d > pd.Timestamp(end)):
            continue
        parts.append(pd.read_csv(snap_path(ds, d), dtype=str, encoding="utf-8-sig"))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


# ---------------- 종목 시세 ----------------
NUM = {"TDD_OPNPRC": "Open", "TDD_HGPRC": "High", "TDD_LWPRC": "Low", "TDD_CLSPRC": "Close",
       "ACC_TRDVOL": "Volume", "CMPPREVDD_PRC": "Chg", "ACC_TRDVAL": "Value",
       "MKTCAP": "MarketCap", "LIST_SHRS": "Shares"}


def tidy_stock(raw: pd.DataFrame) -> pd.DataFrame:
    """원 스냅샷(문자열) → 숫자형 long 표 (Date, code, ...)."""
    if raw.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"Date": pd.to_datetime(raw["BAS_DD"], format="%Y%m%d"),
                       "code": raw["ISU_CD"].astype(str).str.zfill(6),
                       "name": raw.get("ISU_NM")})
    for src, dst in NUM.items():
        df[dst] = pd.to_numeric(raw[src].astype(str).str.replace(",", ""), errors="coerce")
    return df


def adjust(one: pd.DataFrame) -> pd.DataFrame:
    """한 종목의 원시 일봉(Date 오름차순) → 수정 OHLCV.

    거래정지일(시가 0)은 O/H/L 을 비우고 종가는 그대로 둔다(그날 수익률 0).
    """
    one = one.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    close = one["Close"].astype(float)
    prev = close.shift(1)
    base = close - one["Chg"].astype(float)
    f = (base / prev).where((prev > 0) & (base > 0), 1.0)
    f = f.where((f - 1).abs() > 1e-9, 1.0)
    # 60일 넘게 끊겼다 다시 나타난 코드(재상장·코드 재사용)는 이어 붙이지 않는다
    gap = one["Date"].diff().dt.days
    f = f.where(~(gap > 60), 1.0)
    # 수정가_t = 원시_t × Π_{s>t} f_s
    mult = f[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    out = pd.DataFrame({"Date": one["Date"]})
    halted = one["Open"].fillna(0) <= 0
    for c in ("Open", "High", "Low"):
        out[c] = (one[c].astype(float) * mult).where(~halted)
    out["Close"] = close * mult
    out["Volume"] = (one["Volume"].astype(float) / mult).round()
    out["factor"] = f
    return out.set_index("Date")


def build_prices(tidy: pd.DataFrame, codes=None, source: str = "krx_api") -> dict[str, int]:
    """tidy long 표 → prices/<code>.csv (전체 덮어쓰기). {code: 행수}"""
    from src.universe import prices

    out = {}
    if tidy.empty:
        return out
    want = None if codes is None else set(codes)
    for code, one in tidy.groupby("code", sort=False):
        if want is not None and code not in want:
            continue
        one = one[one["Close"] > 0]
        if one.empty:
            continue
        adj = adjust(one)
        prices.save(code, adj[prices.COLS], source, overwrite=True)
        out[code] = len(adj)
    return out


def build_marketcap(tidy: pd.DataFrame, days=None) -> int:
    """tidy → market_cap/<YYYYMMDD>.csv (기존 파일 형식 유지)."""
    from src.universe import marketcap

    n = 0
    want = None if days is None else {pd.Timestamp(d) for d in days}
    for d, g in tidy.groupby("Date"):
        if want is not None and d not in want:
            continue
        t = pd.DataFrame({"close": g["Close"].values, "market_cap": g["MarketCap"].values,
                          "volume": g["Volume"].values, "value": g["Value"].values,
                          "shares": g["Shares"].values}, index=pd.Index(g["code"].values, name="code"))
        if (t["market_cap"].fillna(0) > 0).any():
            marketcap.save(d, t)
            n += 1
    return n


def names_from(tidy: pd.DataFrame) -> dict[str, str]:
    """각 종목의 마지막 이름. 상폐 종목 이름도 여기서 나온다."""
    if tidy.empty or "name" not in tidy:
        return {}
    last = tidy.sort_values("Date").groupby("code")["name"].last()
    return {c: n for c, n in last.items() if isinstance(n, str) and n}


# ---------------- 벤치마크 ----------------
def build_bench() -> dict[str, int]:
    from src.universe import prices

    out = {}
    cache: dict[str, pd.DataFrame] = {}
    for name, (ds, col, val) in BENCH_SOURCES.items():
        if ds not in cache:
            cache[ds] = load_snapshots(ds)
        raw = cache[ds]
        if raw.empty:
            continue
        key = raw[col].astype(str).str.replace(" ", "")
        sel = raw[key == val.replace(" ", "")]
        if sel.empty:
            continue
        if ds == "etf":
            t = tidy_stock(sel)
            adj = adjust(t[t["Close"] > 0])[prices.COLS]
        else:
            df = pd.DataFrame({"Date": pd.to_datetime(sel["BAS_DD"], format="%Y%m%d")})
            for s, d in {"OPNPRC_IDX": "Open", "HGPRC_IDX": "High", "LWPRC_IDX": "Low",
                         "CLSPRC_IDX": "Close", "ACC_TRDVOL": "Volume"}.items():
                df[d] = pd.to_numeric(sel[s].astype(str).str.replace(",", ""), errors="coerce").values
            adj = df.drop_duplicates("Date").set_index("Date").sort_index()
            adj = adj[adj["Close"] > 0]
        prices._save_path(prices.bench_path(name), adj, "krx_api", overwrite=True)
        out[name] = len(adj)
    return out
