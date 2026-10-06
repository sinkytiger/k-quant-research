import json

import pandas as pd
import pytest

from src.factcheck import transcripts as tr


@pytest.mark.parametrize("iso,want", [
    ("PT1H2M3S", 3723),
    ("PT59S", 59),
    ("PT3M", 180),
    ("P1DT1S", 86401),
    ("", None),
    (None, None),
])
def test_parse_duration(iso, want):
    assert tr.parse_duration(iso) == want


def test_is_excluded():
    assert tr.is_excluded("OO투자증권 공식") == "증권"
    assert tr.is_excluded("주식왕", "무료 리딩방 안내") == "리딩"
    assert tr.is_excluded("주식왕", "매일 시황") is None


def test_labels_are_deterministic_and_hide_order():
    ids = [f"UC{i:02d}" for i in range(10)]
    a, b = tr.assign_labels(ids), tr.assign_labels(list(reversed(ids)))
    assert a == b
    assert sorted(a.values()) == list("ABCDEFGHIJ")
    assert [a[i] for i in ids] != list("ABCDEFGHIJ")


def test_freeze_writes_hash_and_refuses_refreeze(tmp_data, monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "hash_path", lambda: tmp_path / "docs" / "channels.sha256")
    (tmp_data / "factcheck").mkdir()
    picked = pd.DataFrame([{"channel_id": f"UC{i}", "title": f"t{i}", "handle": None, "subscribers": 100 - i}
                           for i in range(10)])
    h = tr.freeze(picked)
    assert tr.hash_path().read_text().split()[0] == h
    assert len(tr.load_channels()) == 10
    with pytest.raises(RuntimeError):
        tr.freeze(picked)
    doc = json.loads(tr.channels_path().read_text(encoding="utf-8"))
    doc["channels"].pop()
    tr.channels_path().write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RuntimeError, match="해시"):
        tr.load_channels()
