"""팩트체크용 유튜브 채널 선정·자막 수집 (docs/factcheck/factcheck-기획.md 2·3절).

  python scripts/collect_transcripts.py --candidates          # 1~3단계: 후보·구독자·이름 제외 → candidates.csv
  python scripts/collect_transcripts.py --select [--exclude UCxxx="사유" ...]
                                                               # 4~5단계 + 고정: channels.json, docs/factcheck/channels.sha256
  python scripts/collect_transcripts.py --transcripts         # 고정된 채널의 구간 영상 자막 (재개 가능)
  python scripts/collect_transcripts.py --status

채널 목록·영상·자막은 KQ_DATA_DIR/factcheck/ 에만 저장한다(채널 익명, 기획서 10절).
--select 결과를 확인하고 docs/factcheck/channels.sha256 을 커밋한 뒤에 --transcripts 를 돈다.
"""
from __future__ import annotations

import argparse
import sys

import _boot  # noqa: F401
import pandas as pd

from src import cli
from src.factcheck import transcripts as tr


def parse_excludes(items: list[str]) -> dict[str, str]:
    out = {}
    for s in items or []:
        cid, _, why = s.partition("=")
        if not why:
            sys.exit(f"--exclude 는 채널ID=사유 형식: {s}")
        out[cid.strip()] = why.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidates", action="store_true")
    g.add_argument("--select", action="store_true")
    g.add_argument("--transcripts", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--exclude", nargs="*", help="기획서 3.1 수작업 제외 (1회): 채널ID=사유")
    a = ap.parse_args()
    log = cli.setup("collect_transcripts")
    for sub in ("", "videos", "transcripts"):
        (tr.fc_dir() / sub).mkdir(parents=True, exist_ok=True)
    cand_csv = tr.fc_dir() / "candidates.csv"

    if a.candidates:
        df = tr.collect_candidates(log)
        df.to_csv(cand_csv, index=False, encoding="utf-8-sig")
        log.info("후보 %d, 이름 제외 %d → %s", len(df), df["excluded_by"].notna().sum(), cand_csv)
        print(df[df["excluded_by"].isna()].head(25)[["channel_id", "title", "subscribers", "subscriber_text"]]
              .to_string(index=False))
        return

    if a.select:
        excl = parse_excludes(a.exclude)
        picked = tr.select_channels(pd.read_csv(cand_csv), log, excl)
        if len(picked) < tr.N_CHANNELS:
            sys.exit(f"조건을 만족한 채널이 {len(picked)}개뿐이다. 고정하지 않음")
        print(picked[["channel_id", "title", "subscribers"]].to_string(index=False))
        h = tr.freeze(picked, excl)
        log.info("고정 완료. sha256 %s → docs/factcheck/channels.sha256 을 커밋할 것", h)
        return

    if a.transcripts:
        stats = {"ok": 0, "skip": 0, "none": 0, "error": 0}
        for ch in tr.load_channels():
            vids = pd.read_csv(tr.fc_dir() / "videos" / f"{ch['channel_id']}.csv")
            vids = vids[vids["in_period"]]
            for vid in vids["video_id"]:
                try:
                    stats[tr.fetch_transcript(ch["channel_id"], vid)] += 1
                except Exception as e:  # noqa: BLE001
                    stats["error"] += 1
                    log.warning("채널 %s %s: %s", ch["label"], vid, e)
            log.info("채널 %s 완료 %s", ch["label"], stats)
        return

    if a.status:
        if not tr.channels_path().exists():
            print("채널 미고정. candidates.csv:", cand_csv.exists())
            return
        for ch in tr.load_channels():
            vids = pd.read_csv(tr.fc_dir() / "videos" / f"{ch['channel_id']}.csv")
            got = len(list((tr.fc_dir() / "transcripts" / ch["channel_id"]).glob("*.json")))
            print(f"채널 {ch['label']}: 구간 영상 {int(vids['in_period'].sum())}, 자막 {got}")


if __name__ == "__main__":
    main()
