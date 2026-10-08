"""데이터 상태 표: 항목별 지연 기준 판정."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_dashboard as b  # noqa: E402


def test_state_thresholds_per_item():
    assert b._state(None, 1, 3) == ("critical", "없음")
    assert b._state(1, 1, 3) == ("good", "정상")
    assert b._state(2, 1, 3) == ("warning", "2영업일 지연")
    assert b._state(4, 1, 3) == ("serious", "4영업일 지연")
    # 신용잔고는 결제일 공시라 3영업일까지 정상
    assert b._state(3, 3, 5)[0] == "good"
    assert b._state(9, 8, 15, "일") == ("warning", "9일 지연")
