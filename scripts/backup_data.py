"""수집 데이터 백업: KQ_DATA_DIR 를 zip 으로 묶어 OneDrive\KQuantBackup 에 둔다 (최근 KQ_BACKUP_KEEP 개, 기본 2).

  python scripts/backup_data.py               # run_weekly.bat 에서 돈다
  python scripts/backup_data.py --list        # 지금 있는 백업
  python scripts/backup_data.py --dest D:\백업  # 다른 곳(외장 디스크 등)에

venv·토큰 파일은 넣지 않는다. 복원은 scripts/restore_data.py.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import _boot  # noqa: F401

from src import backup, cli, config


def default_dest() -> Path:
    od = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    return (Path(od) if od else Path.home()) / "KQuantBackup"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=os.environ.get("KQ_BACKUP_DIR") or str(default_dest()))
    ap.add_argument("--keep", type=int, default=int(os.environ.get("KQ_BACKUP_KEEP", "2")))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    log = cli.setup("backup_data")
    dest = Path(a.dest)
    if a.list:
        for z in sorted(dest.glob(f"{backup.PREFIX}*.zip")):
            print(f"{z.name}  {z.stat().st_size / 1024 ** 2:,.0f}MB")
        return 0
    if config.DATA.resolve() == (config.ROOT / "data").resolve() and not config.DATA.exists():
        log.error("데이터 폴더가 없다: %s", config.DATA)
        return 1
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True, text=True).stdout.strip()
    t = time.time()
    out, man = backup.make(config.DATA, dest, {"git": commit})
    log.info("백업 %s: 파일 %s개, 원본 %.0fMB → zip %.0fMB (%.0f초)", out, f"{man['files']:,}", man["bytes"] / 1024 ** 2,
             man["zip_bytes"] / 1024 ** 2, time.time() - t)
    for f in backup.rotate(dest, a.keep):
        log.info("오래된 백업 삭제: %s", f.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
