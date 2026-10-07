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


def test_is_excluded_media():
    assert tr.is_excluded("SBS Biz 뉴스") == "Biz"
    assert tr.is_excluded("김어준의 겸손은힘들다 뉴스공장") == "뉴스공장"
    assert tr.is_excluded("MBCNEWS") == "NEWS"
    assert tr.is_excluded("MBC 경제") == "MBC"
    assert tr.is_excluded("ytn news") == "NEWS"


def test_topic_shares_and_rule():
    titles = ["코스피 급락 이유", "이 종목 매수 타이밍", "엔비디아 실적", "오늘 점심 메뉴", None]
    kr, fo = tr.topic_shares(titles)
    assert kr == pytest.approx(2 / 4) and fo == pytest.approx(1 / 4)
    assert tr.topic_ok(kr, fo)
    assert not tr.topic_ok(0.39, 0.0)
    assert not tr.topic_ok(0.5, 0.6)
    assert tr.topic_shares([]) == (0.0, 0.0)
    assert tr.topic_shares(["s&p500 전망"])[1] == 1.0


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


def test_api_error_does_not_leak_key(monkeypatch):
    class R:
        ok, status_code, reason, text = False, 404, "Not Found", "{}"
        def json(self):
            return {"error": {"message": "playlist not found"}}
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaSECRET")
    monkeypatch.setattr(tr.requests, "get", lambda *a, **k: R())
    with pytest.raises(tr.ApiError) as e:
        tr.call("playlistItems", {"playlistId": "UUx"})
    assert "AIzaSECRET" not in str(e.value) and e.value.status == 404


def test_list_videos_empty_channel(monkeypatch):
    def boom(*a, **k):
        raise tr.ApiError("playlistItems", 404, "not found")
    monkeypatch.setattr(tr, "call", boom)
    df = tr.list_videos("UUx", "2025-10-01", "2026-09-30")
    assert df is not None and len(df) == 0
