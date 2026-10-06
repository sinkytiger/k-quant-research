"""유튜브 채널 선정·영상 목록·자막 수집.

- 채널·영상 정보: YouTube Data API v3 (공식, 무료 일 10,000단위). 키는 YOUTUBE_API_KEY.
  search.list 100단위/호출, channels·playlistItems·videos.list 1단위/호출(50건).
- 자막: yt-dlp (비공식). 남의 영상 자막을 받는 공식 수단이 없어 K-quant "공식 API만" 원칙의 예외다
  (기획서 v1.1, 2절). 차단을 피하려고 영상 사이 대기, 받은 것은 건너뛰어 재개.
- 채널 선정은 기획서 3.1 절차를 그대로 코드로 옮긴 것이다. 사람이 고르지 않는다.
- 채널 목록·영상 ID·자막은 채널을 특정할 수 있으므로 KQ_DATA_DIR/factcheck/ 에만 둔다(기획서 10절).
  저장소에는 channels.json 의 SHA-256 해시만 커밋한다.
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

API = "https://www.googleapis.com/youtube/v3"

# 기획서 3.1
SEARCH_QUERIES = ["주식", "주식투자", "증시", "국내주식", "종목추천", "시황"]
SEARCH_PAGES = 3
EXCLUDE_WORDS = ["증권", "자산운용", "투자증권", "거래소", "리딩", "유료방", "카톡방",
                 "방송", "Biz", "경제TV", "한경", "매경", "머니투데이", "뉴스공장",  # v1.2: 언론사 계열
                 "뉴스", "NEWS", "KBS", "MBC", "SBS", "JTBC", "YTN", "MBN", "TV조선", "채널A"]  # v1.3
# v1.2 주제 기준: 구간 영상 제목에서 단어 포함 비율
DOMESTIC_WORDS = ["주식", "종목", "코스피", "코스닥", "증시", "국장", "매수", "매도", "차트", "수급", "공시",
                  "상한가", "하한가", "테마주", "급등주", "삼성전자", "하이닉스", "2차전지"]
FOREIGN_WORDS = ["미국", "미장", "나스닥", "S&P", "다우", "뉴욕증시", "엔비디아", "테슬라", "애플", "팔란티어",
                 "비트코인", "코인"]
MIN_DOMESTIC_SHARE = 0.40
MIN_VIDEOS = 50
MAX_VIDEOS = 3000  # v1.3: 구간 영상이 이보다 많으면(하루 8개 초과) 클립·언론사형으로 보고 목록 넘기기를 멈춤
MIN_SECONDS = 180  # 3분 이하(쇼츠 포함)는 영상으로 세지 않는다
N_CHANNELS = 10
PERIOD = ("2025-10-01", "2026-09-30")
LABEL_SEED = 42
SUB_LANGS = ["ko"]
SLEEP = (3.0, 6.0)  # 자막 요청 사이 대기(초)


def fc_dir() -> Path:
    return config.DATA / "factcheck"


def channels_path() -> Path:
    return fc_dir() / "channels.json"


def hash_path() -> Path:
    return config.ROOT / "docs" / "factcheck" / "channels.sha256"


# ---------------------------------------------------------------- YouTube Data API

def call(resource: str, params: dict, retries: int = 4) -> dict:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError(".env 에 YOUTUBE_API_KEY 가 없다")
    for i in range(retries):
        r = requests.get(f"{API}/{resource}", params={**params, "key": key}, timeout=60)
        if r.status_code in (500, 503):
            time.sleep(2 ** i)
            continue
        if r.status_code == 403 and "quotaExceeded" in r.text:
            raise RuntimeError("YouTube API 일 할당량 소진. 내일(태평양 자정) 이어서 실행")
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"{resource} 재시도 {retries}회 실패")


def chunks(xs: list, n: int = 50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def parse_duration(iso: str | None) -> int | None:
    """'PT1H2M3S' → 3723초."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m or not iso:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def is_excluded(title: str, description: str = "") -> str | None:
    """기획서 3.1 규칙 3. 걸린 단어를 돌려준다."""
    text = f"{title} {description}".lower()
    return next((w for w in EXCLUDE_WORDS if w.lower() in text), None)


def topic_shares(titles) -> tuple[float, float]:
    """(국내 주식 단어 포함 비율, 해외·코인 단어 포함 비율). 제목이 없으면 (0, 0)."""
    ts = [str(t) for t in titles if isinstance(t, str) and t]
    if not ts:
        return 0.0, 0.0
    kr = sum(any(w in t for w in DOMESTIC_WORDS) for t in ts) / len(ts)
    fo = sum(any(w.lower() in t.lower() for w in FOREIGN_WORDS) for t in ts) / len(ts)
    return kr, fo


def topic_ok(kr: float, fo: float) -> bool:
    """기획서 3.1 6단계: 국내 비율 40% 이상이고 해외 비율이 국내보다 낮음."""
    return kr >= MIN_DOMESTIC_SHARE and fo < kr


def assign_labels(channel_ids: list[str], seed: int = LABEL_SEED) -> dict[str, str]:
    """구독자 순서가 드러나지 않게 무작위로 A, B, C ... (기획서 10절)."""
    ids = sorted(channel_ids)
    random.Random(seed).shuffle(ids)
    return {cid: chr(ord("A") + i) for i, cid in enumerate(ids)}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 채널 선정 (기획서 3.1)

def collect_candidates(log) -> pd.DataFrame:
    """1~3단계: 검색 후보 → 구독자 수 → 이름 제외. 할당량 약 1,800단위."""
    seen: dict[str, list[str]] = {}
    for q in SEARCH_QUERIES:
        token = None
        for _ in range(SEARCH_PAGES):
            body = call("search", {"part": "snippet", "type": "channel", "q": q, "regionCode": "KR",
                                   "relevanceLanguage": "ko", "maxResults": 50,
                                   **({"pageToken": token} if token else {})})
            for it in body.get("items", []):
                seen.setdefault(it["id"]["channelId"], []).append(q)
            token = body.get("nextPageToken")
            if not token:
                break
        log.info("검색 '%s' 후 후보 %d", q, len(seen))
    rows = []
    for ids in chunks(list(seen)):
        body = call("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "maxResults": 50})
        for it in body.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            hidden = st.get("hiddenSubscriberCount", False)
            rows.append({"channel_id": it["id"], "title": sn.get("title", ""), "handle": sn.get("customUrl"),
                         "subscribers": None if hidden else int(st.get("subscriberCount", 0)),
                         "video_count": int(st.get("videoCount", 0)),
                         "uploads": it["contentDetails"]["relatedPlaylists"]["uploads"],
                         "queries": ",".join(seen[it["id"]]),
                         "description": (sn.get("description") or "")[:500],
                         "excluded_by": is_excluded(sn.get("title", ""), sn.get("description", ""))})
    df = pd.DataFrame(rows).sort_values("subscribers", ascending=False, na_position="last")
    return df.reset_index(drop=True)


def list_videos(uploads: str, start: str, end: str) -> pd.DataFrame | None:
    """업로드 재생목록(최신순)을 넘기며 start 이전이 나오면 멈춘다. 길이는 videos.list 로.
    받은 영상이 MAX_VIDEOS 를 넘으면 None (기획서 3.1 4단계 상한)."""
    rows, token = [], None
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    while True:
        body = call("playlistItems", {"part": "contentDetails,snippet", "playlistId": uploads, "maxResults": 50,
                                      **({"pageToken": token} if token else {})})
        items = body.get("items", [])
        for it in items:
            pub = it["contentDetails"].get("videoPublishedAt")
            rows.append({"video_id": it["contentDetails"]["videoId"], "title": it["snippet"].get("title"),
                         "published": pd.Timestamp(pub) if pub else None})
        token = body.get("nextPageToken")
        dates = [r["published"] for r in rows[-len(items):] if r["published"] is not None]
        if not token or (dates and min(dates) < lo):
            break
        if len(rows) > MAX_VIDEOS:
            return None  # 상한 초과: 목록을 끝까지 받지 않음
    df = pd.DataFrame(rows, columns=["video_id", "title", "published"])
    df["in_period"] = df["published"].notna() & (df["published"] >= lo) & (df["published"] < hi)
    dur = {}
    for ids in chunks(df.loc[df["in_period"], "video_id"].tolist()):
        for it in call("videos", {"part": "contentDetails", "id": ",".join(ids)}).get("items", []):
            dur[it["id"]] = parse_duration(it["contentDetails"].get("duration"))
    df["seconds"] = df["video_id"].map(dur)
    df["in_period"] &= df["seconds"].fillna(0) > MIN_SECONDS
    return df


def select_channels(cands: pd.DataFrame, log, manual_exclude: dict[str, str] | None = None) -> pd.DataFrame:
    """4~6단계: 구독자 순으로 내려가며 수집 구간 영상(3분 초과) 50개 이상이고 주제 기준을 넘는 채널 10개.
    manual_exclude = {channel_id: 사유} 는 기획서 3.1의 1회 수작업 제외."""
    manual_exclude = manual_exclude or {}
    picked, checked = [], []
    ok_rows = cands[cands["excluded_by"].isna() & cands["subscribers"].notna()]
    for row in ok_rows.itertuples(index=False):
        if row.channel_id in manual_exclude:
            checked.append({"channel_id": row.channel_id, "status": "manual_exclude",
                            "reason": manual_exclude[row.channel_id]})
            continue
        vids = list_videos(row.uploads, *PERIOD)
        if vids is None:
            checked.append({"channel_id": row.channel_id, "title": row.title, "status": "too_many_videos"})
            log.info("%s 구독자 %s 구간 영상 %d개 초과 → too_many_videos", row.title, row.subscribers, MAX_VIDEOS)
            continue
        n = int(vids["in_period"].sum())
        kr, fo = topic_shares(vids.loc[vids["in_period"], "title"])
        status = ("too_few_videos" if n < MIN_VIDEOS else "off_topic" if not topic_ok(kr, fo) else "picked")
        ok = status == "picked"
        checked.append({"channel_id": row.channel_id, "title": row.title, "status": status, "n_videos": n,
                        "domestic_share": round(kr, 3), "foreign_share": round(fo, 3)})
        log.info("%s 구독자 %s 구간 영상 %d 국내 %.0f%% 해외 %.0f%% → %s",
                 row.title, row.subscribers, n, kr * 100, fo * 100, status)
        if ok:
            picked.append(row._asdict())
            vids.to_csv(fc_dir() / "videos" / f"{row.channel_id}.csv", index=False, encoding="utf-8-sig")
        if len(picked) == N_CHANNELS:
            break
    pd.DataFrame(checked).to_csv(fc_dir() / "selection_log.csv", index=False, encoding="utf-8-sig")
    return pd.DataFrame(picked)


def freeze(picked: pd.DataFrame, manual_exclude: dict[str, str] | None = None) -> str:
    """channels.json 저장(저장소 밖)하고 해시를 docs/factcheck/channels.sha256 에 쓴다."""
    p = channels_path()
    if p.exists():
        raise RuntimeError(f"{p} 가 이미 있다. 채널 목록은 한 번 고정하면 바꾸지 않는다(기획서 3.1)")
    labels = assign_labels(picked["channel_id"].tolist())
    doc = {"frozen_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="seconds"),
           "procedure": "docs/factcheck/factcheck-기획.md 3.1", "period": PERIOD,
           "manual_exclude": manual_exclude or {},
           "channels": [{"label": labels[r["channel_id"]], "channel_id": r["channel_id"], "title": r["title"],
                         "handle": r.get("handle"), "subscribers": r["subscribers"]}
                        for r in picked.to_dict("records")]}
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


# ---------------------------------------------------------------- 자막 (yt-dlp)

def transcript_dir(channel_id: str) -> Path:
    return fc_dir() / "transcripts" / channel_id


def has_transcript(channel_id: str, video_id: str) -> bool:
    d = transcript_dir(channel_id)
    return any(d.glob(f"{video_id}.*.json3")) or (d / f"{video_id}.none").exists()


def fetch_transcript(channel_id: str, video_id: str) -> str:
    """수동 한국어 자막이 있으면 그것, 없으면 자동 생성 자막을 json3(타임스탬프 포함)로 저장.
    반환: ok / skip / none(자막 없음 — 표식 파일을 남겨 재시도하지 않음)."""
    import yt_dlp

    if has_transcript(channel_id, video_id):
        return "skip"
    d = transcript_dir(channel_id)
    d.mkdir(parents=True, exist_ok=True)
    opts = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": SUB_LANGS, "subtitlesformat": "json3",
            "outtmpl": str(d / "%(id)s.%(ext)s"), "quiet": True, "no_warnings": True,
            "sleep_interval_requests": 1}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={video_id}"])
    time.sleep(random.uniform(*SLEEP))
    if any(d.glob(f"{video_id}.*.json3")):
        return "ok"
    (d / f"{video_id}.none").touch()
    return "none"
