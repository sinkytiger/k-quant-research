"""백업 복원 (새 PC 로 옮길 때): zip 을 KQ_DATA_DIR(.env) 에 푼다. 폴더가 비어 있지 않으면 --force 없이는 멈춘다.

  python scripts/restore_data.py "C:\Users\me\OneDrive\KQuantBackup\kquant-data-20261010-0930.zip"
  python scripts/restore_data.py --latest        # OneDrive\KQuantBackup 의 가장 최근 백업
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _boot  # noqa: F401

from src import backup, cli, config
from backup_data import default_dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", nargs="?")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--force", action="store_true", help="데이터 폴더에 이미 파일이 있어도 덮어쓴다")
    a = ap.parse_args(argv)
    log = cli.setup("restore_data")
    if a.latest:
        zips = sorted(default_dest().glob(f"{backup.PREFIX}*.zip"))
        if not zips:
            log.error("백업이 없다: %s", default_dest())
            return 1
        z = zips[-1]
    elif a.zip:
        z = Path(a.zip)
    else:
        ap.print_help()
        return 1
    man = backup.restore(z, config.DATA, force=a.force)
    log.info("복원 완료: %s → %s (파일 %s개, 백업 시각 %s, 코드 %s)", z.name, config.DATA, f"{man['files']:,}", man["created"], man.get("git"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
