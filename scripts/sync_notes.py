"""분석 노트(notes/)를 Git 에 자동 저장: 바뀐 게 있으면 notes/ 만 커밋하고 비공개 저장소로 push.

  python scripts/sync_notes.py            # run_daily.bat 끝에서 돈다
  python scripts/sync_notes.py --dry-run  # 무엇을 커밋할지만 보여 준다

- notes/ 경로만 커밋한다(`git commit -- notes/`). 다른 작업 중인 파일은 건드리지 않는다.
- 50MB 를 넘는 파일은 커밋하지 않고 경고만 남긴다 (GitHub 는 100MB 부터 거부).
- push 가 거절되면(원격이 앞서 있음) 강제로 덮지 않는다. 커밋은 로컬에 남고 다음 push 때 같이 올라간다.
- 공개 저장소 반영은 자동으로 하지 않는다 (scripts/publish_public.py --push 를 따로 실행).
"""
from __future__ import annotations

import argparse
import subprocess
import sys

import _boot  # noqa: F401

from src import cli, config

MAX_MB = 50


def git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=config.ROOT, check=check,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def parse_status(porcelain: str) -> list[tuple[str, str]]:
    """`git status --porcelain=v1 -z` 출력 → [(상태, 경로)]. 이름 바꾸기는 새 경로만."""
    out, parts, i = [], porcelain.split("\0"), 0
    while i < len(parts):
        e = parts[i]
        if len(e) < 4:
            i += 1
            continue
        st, path = e[:2], e[3:]
        out.append((st, path))
        i += 2 if "R" in st or "C" in st else 1  # 이름 바꾸기는 옛 경로가 다음 칸에 온다
    return out


def message(changes: list[tuple[str, str]]) -> str:
    """'분석 노트: 추가 2, 수정 1, 삭제 0' + 파일 이름 목록."""
    add = [p for s, p in changes if "?" in s or "A" in s]
    dele = [p for s, p in changes if "D" in s]
    mod = [p for s, p in changes if p not in add and p not in dele]
    head = f"분석 노트: 추가 {len(add)}, 수정 {len(mod)}, 삭제 {len(dele)}"
    names = [f"+ {p}" for p in add] + [f"~ {p}" for p in mod] + [f"- {p}" for p in dele]
    return head + ("\n\n" + "\n".join(names[:30]) if names else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("sync_notes")

    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":
        log.warning("main 브랜치가 아니라서 건너뛴다")
        return 0
    if (config.ROOT / ".git" / "MERGE_HEAD").exists() or (config.ROOT / ".git" / "rebase-merge").exists():
        log.warning("병합·리베이스 중이라 건너뛴다")
        return 0
    changes = parse_status(git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "notes/").stdout)
    big = [p for s, p in changes if "D" not in s and (config.ROOT / p).exists()
           and (config.ROOT / p).stat().st_size > MAX_MB * 1024 * 1024]
    for p in big:
        log.warning("%dMB 넘는 파일은 커밋하지 않는다: %s", MAX_MB, p)
    changes = [(s, p) for s, p in changes if p not in big]
    if not changes:
        log.info("notes/ 변경 없음")
        return 0
    msg = message(changes)
    log.info("%s", msg.replace("\n", " | "))
    if a.dry_run:
        return 0
    paths = [p for _, p in changes]
    git("add", "-A", "--", *paths)
    git("commit", "-q", "-m", msg, "--", *paths)
    log.info("커밋 %s", git("rev-parse", "--short", "HEAD").stdout.strip())
    push = git("push", "-q", check=False)
    if push.returncode != 0:
        log.warning("push 실패 (커밋은 로컬에 남는다, 다음에 다시): %s", (push.stderr or push.stdout).strip()[:300])
        return 2
    log.info("push 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
