"""`python scripts/xxx.py` 로 실행해도 `from src import ...` 가 되게 한다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
