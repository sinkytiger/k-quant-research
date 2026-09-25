import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """모든 데이터 경로를 임시 폴더로 돌린다. 실제 데이터는 건드리지 않는다."""
    uni = tmp_path / "universe"
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "UNIVERSE_DIR", uni)
    monkeypatch.setattr(config, "MEMBERSHIP_DIR", uni / "membership")
    monkeypatch.setattr(config, "PRICES_DIR", uni / "prices")
    monkeypatch.setattr(config, "MARKETCAP_DIR", uni / "market_cap")
    monkeypatch.setattr(config, "BENCH_DIR", uni / "bench")
    monkeypatch.setattr(config, "NAMES_CSV", uni / "names.csv")
    monkeypatch.setattr(config, "FLOWS_DIR", tmp_path / "flows")
    return tmp_path
