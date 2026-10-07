"""데이터 백업: 제외 규칙(venv·토큰), 검증, 보관 개수, 복원(비어 있지 않으면 거부)."""
from datetime import datetime

import pytest

from src import backup


def _data(root):
    (root / "raw" / "krx").mkdir(parents=True)
    (root / "raw" / "krx" / "a.json").write_text("{}", encoding="utf-8")
    (root / "venv-gsq" / "lib").mkdir(parents=True)
    (root / "venv-gsq" / "lib" / "x.py").write_text("x", encoding="utf-8")
    (root / ".kis_token_prod.json").write_text("secret", encoding="utf-8")
    (root / "flows.csv").write_text("a,b\n1,2\n", encoding="utf-8")


def test_make_excludes_and_verifies(tmp_path):
    src, dest = tmp_path / "data", tmp_path / "bk"
    _data(src)
    z, man = backup.make(src, dest, {"git": "abc"}, now=datetime(2026, 10, 10, 9, 30))
    assert z.name == "kquant-data-20261010-0930.zip" and man["files"] == 2 and man["git"] == "abc"
    import zipfile
    names = zipfile.ZipFile(z).namelist()
    assert "raw/krx/a.json" in names and "flows.csv" in names
    assert not any("venv" in n or "kis_token" in n for n in names)


def test_rotate_keeps_latest(tmp_path):
    for d in ("20261001-0930", "20261003-0930", "20261010-0930"):
        (tmp_path / f"kquant-data-{d}.zip").write_bytes(b"x")
    gone = backup.rotate(tmp_path, keep=2)
    assert [g.name for g in gone] == ["kquant-data-20261001-0930.zip"]
    assert backup.rotate(tmp_path, keep=0) and len(list(tmp_path.glob("*.zip"))) == 1  # 최소 1개는 남긴다


def test_restore_refuses_nonempty(tmp_path):
    src, dest, new = tmp_path / "data", tmp_path / "bk", tmp_path / "new"
    _data(src)
    z, _ = backup.make(src, dest)
    backup.restore(z, new)
    assert (new / "raw" / "krx" / "a.json").exists() and not (new / ".kis_token_prod.json").exists()
    with pytest.raises(FileExistsError):
        backup.restore(z, new)
    backup.restore(z, new, force=True)


def test_copy_to_second_place(tmp_path):
    src, a, b = tmp_path / "data", tmp_path / "a", tmp_path / "b"
    _data(src)
    z, _ = backup.make(src, a)
    out = backup.copy_to(z, b)
    assert out.read_bytes() == z.read_bytes() and not list(b.glob("*.part"))
    backup.verify(out)
