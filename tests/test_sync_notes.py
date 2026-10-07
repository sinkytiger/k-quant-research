"""노트 자동 커밋: git status 해석(이름 바꾸기·공백 경로), 커밋 메시지."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_notes as sn  # noqa: E402


def test_parse_status_handles_rename_and_spaces():
    raw = "?? notes/public/2026-10-07_반도체 메모.pdf\0R  notes/new.pdf\0notes/old.pdf\0 D notes/x.pdf\0"
    assert sn.parse_status(raw) == [("??", "notes/public/2026-10-07_반도체 메모.pdf"), ("R ", "notes/new.pdf"), (" D", "notes/x.pdf")]


def test_message_counts():
    m = sn.message([("??", "notes/a.pdf"), (" M", "notes/b.pdf"), (" D", "notes/c.pdf")])
    assert m.startswith("분석 노트: 추가 1, 수정 1, 삭제 1")
    assert "+ notes/a.pdf" in m and "~ notes/b.pdf" in m and "- notes/c.pdf" in m
