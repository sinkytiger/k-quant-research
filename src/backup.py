"""수집 데이터(KQ_DATA_DIR) 백업·복원: zip 한 개 + 안의 manifest.json.

- 다시 만들 수 있는 것(파이썬 가상환경 venv*, __pycache__)과 접속 토큰(.kis_token_*)은 넣지 않는다.
- 임시 폴더에서 만든 뒤 대상 폴더로 옮긴다 (OneDrive 가 만드는 도중의 파일을 동기화하지 않게).
- 만든 뒤 zip 을 다시 열어 파일 수와 CRC 를 확인한다.
- 대상 폴더의 kquant-data-*.zip 은 최근 keep 개만 남긴다.
"""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PREFIX = "kquant-data-"
EXCLUDE_DIRS = ("venv*", ".venv*", "__pycache__")
EXCLUDE_FILES = (".kis_token_*", "*.tmp", "*.lock")


def iter_files(root: Path):
    """백업할 파일 (root 기준 상대 경로 순서대로)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not any(fnmatch.fnmatch(d, p) for p in EXCLUDE_DIRS))
        for f in sorted(filenames):
            if not any(fnmatch.fnmatch(f, p) for p in EXCLUDE_FILES):
                yield Path(dirpath) / f


def make(root: Path, dest_dir: Path, note: dict | None = None, now: datetime | None = None) -> tuple[Path, dict]:
    """root 를 zip 으로 묶어 dest_dir 에 둔다. (경로, manifest)"""
    now = now or datetime.now()
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{PREFIX}{now:%Y%m%d-%H%M}.zip"
    files, total = 0, 0
    with tempfile.TemporaryDirectory(prefix="kq-backup-") as tmp:
        tmp_zip = Path(tmp) / name
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in iter_files(root):
                try:
                    z.write(f, f.relative_to(root).as_posix())
                except (PermissionError, FileNotFoundError):  # 다른 작업이 쓰는 중이거나 그 사이 지워진 파일
                    continue
                files += 1
                total += f.stat().st_size if f.exists() else 0
            manifest = {"created": now.isoformat(timespec="seconds"), "source": str(root), "files": files,
                        "bytes": total, **(note or {})}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        verify(tmp_zip, files)
        manifest["zip_bytes"] = tmp_zip.stat().st_size
        out = dest_dir / name
        shutil.move(str(tmp_zip), out)
    return out, manifest


def verify(zip_path: Path, expect_files: int | None = None) -> dict:
    """CRC 검사와 파일 수 확인. 문제가 있으면 ValueError."""
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad:
            raise ValueError(f"백업 손상: {bad}")
        man = json.loads(z.read("manifest.json"))
        n = len([i for i in z.infolist() if i.filename != "manifest.json"])
    if expect_files is not None and n != expect_files:
        raise ValueError(f"백업 파일 수 불일치: {n} != {expect_files}")
    if n != man["files"]:
        raise ValueError(f"manifest 파일 수 불일치: {n} != {man['files']}")
    return man


def rotate(dest_dir: Path, keep: int) -> list[Path]:
    """오래된 백업을 지우고 지운 목록을 돌려준다 (최근 keep 개 유지, 최소 1개)."""
    zips = sorted(dest_dir.glob(f"{PREFIX}*.zip"))
    old = zips[:-max(1, keep)]
    for f in old:
        f.unlink()
    return old


def restore(zip_path: Path, root: Path, force: bool = False) -> dict:
    """zip 을 root 에 푼다. root 에 이미 파일이 있으면 force 없이는 거부."""
    man = verify(zip_path)
    if root.exists() and any(root.iterdir()) and not force:
        raise FileExistsError(f"{root} 가 비어 있지 않다. 덮어쓰려면 --force")
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.filename == "manifest.json":
                continue
            target = (root / info.filename).resolve()
            if root.resolve() not in target.parents:  # 압축 경로 조작 방지
                raise ValueError(f"잘못된 경로: {info.filename}")
            z.extract(info, root)
    return man
