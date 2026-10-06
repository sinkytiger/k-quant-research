import json

import pandas as pd
import pytest

from src.factcheck import transcripts as tr


@pytest.mark.parametrize("text,want", [
    ("구독자 12.3만명", 123_000),
    ("구독자 1.05천명", 1_050),
    ("구독자 1억명", 100_000_000),
    ("1.2M subscribers", 1_200_000),
    ("345K subscribers", 345_000),
    ("980 subscribers", 980),
    ("1,234 subscribers", 1_234),
    (None, None),
    ("구독자 없음", None),
])
def test_parse_subscribers(text, want):
    assert tr.parse_subscribers(text) == want


def test_is_excluded():
    assert tr.is_excluded("OO투자증권 공식") == "증권"
    assert tr.is_excluded("주식왕", "무료 리딩방 안내") == "리딩"
    assert tr.is_excluded("주식왕", "매일 시황") is None


def test_find_date_nested_and_formats():
    assert tr.find_date({"a": {"publishDate": "2026-03-04T00:00:00"}}) == pd.Timestamp("2026-03-04")
    assert tr.find_date([{"x": 1}, {"upload_date": "20251203"}]) == pd.Timestamp("2025-12-03")
    assert tr.find_date({"publishedTimeText": "3개월 전"}) is None


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
