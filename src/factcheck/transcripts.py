"""유튜브 채널 선정·영상 목록·자막 수집 — transcriptapi.com REST v2.

- 인증: Authorization: Bearer TRANSCRIPT_API_KEY. 분당 300회. 실패(4xx/5xx/429)는 크레딧 차감 없음.
- 채널 선정은 기획서 3.1 절차를 그대로 코드로 옮긴 것이다. 사람이 고르지 않는다.
- 채널 목록·영상 ID·자막은 채널을 특정할 수 있으므로 KQ_DATA_DIR/factcheck/ 에만 둔다(기획서 10절).
  저장소에는 channels.json 의 SHA-256 해시만 커밋한다.
- 게시일: 영상 목록의 publishedTimeText 는 "3개월 전" 같은 상대 표기라 쓰지 않는다.
  /youtube/info(무료) → /youtube/video/metadata(1크레딧) 순으로 절대 날짜를 찾는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import pandas as pd
import requests

from src import config

BASE = "https://transcriptapi.com/api/v2"

# 기획서 3.1
SEARCH_QUERIES = ["주식", "주식투자", "증시", "국내주식", "종목추천", "시황"]
SEARCH_PAGES = 3
EXCLUDE_WORDS = ["증권", "자산운용", "투자증권", "거래소", "리딩", "유료방", "카톡방"]
MIN_VIDEOS = 50
N_CHANNELS = 10
PERIOD = ("2025-10-01", "2026-09-30")
LABEL_SEED = 42
LANG = "ko,asr-ko,asr"


def fc_dir() -> Path:
    return config.DATA / "factcheck"


def channels_path() -> Path:
    return fc_dir() / "channels.json"


def hash_path() -> Path:
    return config.ROOT / "docs" / "factcheck" / "channels.sha256"


# ---------------------------------------------------------------- API

def call(path: str, params: dict, retries: int = 5) -> dict:
    key = os.environ.get("TRANSCRIPT_API_KEY")
    if not key:
        raise RuntimeError(".env 에 TRANSCRIPT_API_KEY 가 없다")
    for i in range(retries):
        r = requests.get(BASE + path, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=60)
        if r.status_code in (408, 429, 503):
            time.sleep(float(r.headers.get("Retry-After") or 2 ** i))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"{path} 재시도 {retries}회 실패")


def paged(path: str, params: dict, max_pages: int) -> list[dict]:
    out, cont = [], None
    for _ in range(max_pages):
        body = call(path, {**params, **({"continuation": cont} if cont else {})})
        out += body.get("results") or []
        cont = body.get("continuation_token")
        if not (body.get("has_more") and cont):
            break
    return out


# ---------------------------------------------------------------- 파싱

_UNIT = {"천": 1e3, "만": 1e4, "억": 1e8, "k": 1e3, "m": 1e6, "b": 1e9}


def parse_subscribers(text: str | None) -> int | None:
    """'구독자 12.3만명' → 123000, '1.2M subscribers' → 1200000, '980 subscribers' → 980. 못 읽으면 None."""
    if not text:
        return None
    m = re.search(r"(\d[\d.,]*)\s*([천만억kKmMbB]?)", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    return int(round(num * _UNIT.get(m.group(2).lower(), 1)))


def is_excluded(title: str, description: str = "") -> str | None:
    """기획서 3.1 규칙 3. 걸린 단어를 돌려준다."""
    text = f"{title} {description}"
    return next((w for w in EXCLUDE_WORDS if w in text), None)


_DATE_KEYS = ("publishDate", "uploadDate", "publishedAt", "published", "upload_date", "publish_date", "date")


def find_date(obj) -> pd.Timestamp | None:
    """응답 JSON 어디에 있든 게시일 키를 찾아 날짜로. 응답 형식이 문서에 없어 키 이름 후보로 찾는다."""
    if isinstance(obj, dict):
        for k in _DATE_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}|\d{8}$", v):
                return pd.Timestamp(v[:10] if "-" in v else f"{v[:4]}-{v[4:6]}-{v[6:8]}")
        for v in obj.values():
            d = find_date(v)
            if d is not None:
                return d
    elif isinstance(obj, list):
        for v in obj:
            d = find_date(v)
            if d is not None:
                return d
    return None


def assign_labels(channel_ids: list[str], seed: int = LABEL_SEED) -> dict[str, str]:
    """구독자 순서가 드러나지 않게 무작위로 A, B, C ... (기획서 10절)."""
    ids = sorted(channel_ids)
    random.Random(seed).shuffle(ids)
    return {cid: chr(ord("A") + i) for i, cid in enumerate(ids)}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 채널 선정 (기획서 3.1)

def collect_candidates(log) -> pd.DataFrame:
    """1~3단계: 검색 후보 → 구독자 수 → 이름 제외. 활동(4단계)은 select_channels 에서."""
    seen: dict[str, dict] = {}
    for q in SEARCH_QUERIES:
        for r in paged("/youtube/search", {"q": q, "type": "channel"}, SEARCH_PAGES):
            cid = r.get("channelId") or r.get("id")
            if cid:
                seen.setdefault(cid, {"channel_id": cid, "queries": []})["queries"].append(q)
        log.info("검색 '%s' 후 후보 %d", q, len(seen))
    rows = []
    for cid, row in seen.items():
        info = call("/youtube/channel/info", {"channel": cid})
        title, desc = info.get("title", ""), info.get("description", "")
        rows.append({**row, "title": title, "handle": info.get("handle"),
                     "subscribers": parse_subscribers(info.get("subscriberCountText")),
                     "subscriber_text": info.get("subscriberCountText"),
                     "excluded_by": is_excluded(title, desc)})
    df = pd.DataFrame(rows).sort_values("subscribers", ascending=False, na_position="last")
    df["queries"] = df["queries"].map(",".join)
    return df.reset_index(drop=True)


def video_date(video_id: str) -> tuple[pd.Timestamp | None, str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    d = find_date(call("/youtube/info", {"video_url": url}))
    if d is not None:
        return d, "info"
    d = find_date(call("/youtube/video/metadata", {"video_url": url, "include": "details"}))
    return d, ("metadata" if d is not None else "none")


def list_videos(channel_id: str, start: str, end: str, max_pages: int = 60) -> pd.DataFrame:
    """최신순 목록을 넘기며 게시일을 확인하고, start 이전 영상이 나오면 멈춘다."""
    rows, cont = [], None
    for _ in range(max_pages):
        body = call("/youtube/channel/videos",
                    {"channel": channel_id, "tab": "videos", "sort": "newest",
                     **({"continuation": cont} if cont else {})})
        stop = False
        for r in body.get("results") or []:
            if r.get("members_only"):
                continue
            d, src = video_date(r["videoId"])
            rows.append({"video_id": r["videoId"], "title": r.get("title"), "published": d, "date_source": src})
            if d is not None and d < pd.Timestamp(start):
                stop = True
        cont = body.get("continuation_token")
        if stop or not (body.get("has_more") and cont):
            break
    df = pd.DataFrame(rows, columns=["video_id", "title", "published", "date_source"])
    df["in_period"] = df["published"].between(pd.Timestamp(start), pd.Timestamp(end))
    return df


def select_channels(cands: pd.DataFrame, log, manual_exclude: dict[str, str] | None = None) -> pd.DataFrame:
    """4~5단계: 구독자 순으로 내려가며 수집 구간 영상 50개 이상인 채널 10개.
    manual_exclude = {channel_id: 사유} 는 기획서 3.1의 1회 수작업 제외."""
    manual_exclude = manual_exclude or {}
    picked, checked = [], []
    for row in cands[cands["excluded_by"].isna() & cands["subscribers"].notna()].itertuples():
        if row.channel_id in manual_exclude:
            checked.append({"channel_id": row.channel_id, "status": "manual_exclude",
                            "reason": manual_exclude[row.channel_id]})
            continue
        vids = list_videos(row.channel_id, *PERIOD)
        n = int(vids["in_period"].sum())
        ok = n >= MIN_VIDEOS
        checked.append({"channel_id": row.channel_id, "status": "picked" if ok else "too_few_videos", "n_videos": n})
        log.info("%s 구독자 %s 구간 영상 %d → %s", row.title, row.subscribers, n, "선정" if ok else "탈락")
        if ok:
            picked.append(row._asdict())
            vids.to_csv(fc_dir() / "videos" / f"{row.channel_id}.csv", index=False, encoding="utf-8-sig")
        if len(picked) == N_CHANNELS:
            break
    pd.DataFrame(checked).to_csv(fc_dir() / "selection_log.csv", index=False, encoding="utf-8-sig")
    return pd.DataFrame(picked).drop(columns=["Index"], errors="ignore")


def freeze(picked: pd.DataFrame, manual_exclude: dict[str, str] | None = None) -> str:
    """channels.json 저장(저장소 밖)하고 해시를 docs/factcheck/channels.sha256 에 쓴다."""
    labels = assign_labels(picked["channel_id"].tolist())
    doc = {"frozen_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="seconds"),
           "procedure": "docs/factcheck/factcheck-기획.md 3.1", "period": PERIOD,
           "manual_exclude": manual_exclude or {},
           "channels": [{"label": labels[r["channel_id"]], "channel_id": r["channel_id"], "title": r["title"],
                         "handle": r.get("handle"), "subscribers": r["subscribers"]}
                        for r in picked.to_dict("records")]}
    p = channels_path()
    if p.exists():
        raise RuntimeError(f"{p} 가 이미 있다. 채널 목록은 한 번 고정하면 바꾸지 않는다(기획서 3.1)")
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    h = file_sha256(p)
    hash_path().parent.mkdir(parents=True, exist_ok=True)
    hash_path().write_text(f"{h}  channels.json\n", encoding="utf-8")
    return h


def load_channels() -> list[dict]:
    p = channels_path()
    want = hash_path().read_text(encoding="utf-8").split()[0]
    if file_sha256(p) != want:
        raise RuntimeError("channels.json 해시가 커밋된 값과 다르다. 고정 후 목록이 바뀌었다")
    return json.loads(p.read_text(encoding="utf-8"))["channels"]


# ---------------------------------------------------------------- 자막

def transcript_path(channel_id: str, video_id: str) -> Path:
    return fc_dir() / "transcripts" / channel_id / f"{video_id}.json"


def fetch_transcript(channel_id: str, video_id: str) -> str:
    """받은 그대로 저장. 이미 있으면 건너뜀(재개). 반환: ok / skip / none(자막 없음)."""
    p = transcript_path(channel_id, video_id)
    if p.exists():
        return "skip"
    try:
        body = call("/youtube/transcript", {"video_url": f"https://www.youtube.com/watch?v={video_id}",
                                            "format": "json", "language": LANG, "include_timestamp": "true",
                                            "send_metadata": "true"})
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return "none"
        raise
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return "ok"
