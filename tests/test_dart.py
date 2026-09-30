"""DART: 12개 분류 규칙(위에서부터), 월 페이징."""
import pandas as pd
import pytest

from src.data import dart


@pytest.mark.parametrize("name,cat", [
    ("[기재정정]주요사항보고서(자기주식취득결정)", "정정공시"),
    ("주요사항보고서(유상증자결정)", "주요사항"),
    ("자기주식취득결과보고서", "자사주"),
    ("증권발행실적보고서", "기타"),
    ("유상증자결정", "자금조달"),
    ("소송등의제기ㆍ신청(일정금액이상의청구)", "리스크"),
    ("주식등의대량보유상황보고서(일반)", "지배구조"),
    ("임원ㆍ주요주주특정증권등소유상황보고서", "지배구조"),
    ("단일판매ㆍ공급계약체결", "수주"),
    ("신규시설투자등", "투자"),
    ("회사합병결정", "M&A"),
    ("분기보고서 (2026.06)", "정기보고"),
    ("감사보고서제출", "감사"),
    ("기업설명회(IR)개최(안내공시)", "기타"),
])
def test_classify_rules_in_priority_order(name, cat):
    assert dart.classify(name) == cat


def test_fetch_range_pages_through_all(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setattr(dart, "MIN_INTERVAL", 0)
    pages = []

    def get(params):
        pages.append(params["page_no"])
        n = params["page_no"]
        rows = [{"rcept_no": f"{n}{i:03d}", "rcept_dt": "20160105", "corp_name": "A", "stock_code": "005930",
                 "corp_cls": "Y", "report_nm": "공시", "rm": ""} for i in range(100 if n < 3 else 28)]
        return {"status": "000", "total_page": 3, "list": rows}

    monkeypatch.setattr(dart, "_get", get)
    df = dart.fetch_range("20160101", "20160131")
    assert pages == [1, 2, 3] and len(df) == 228 and list(df.columns) == dart.COLS


def test_no_data_and_limit(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setattr(dart, "MIN_INTERVAL", 0)
    monkeypatch.setattr(dart, "_get", lambda p: {"status": "013", "message": "조회된 데이타가 없습니다."})
    assert dart.fetch_range("20260925", "20260925").empty
    monkeypatch.setattr(dart, "_get", lambda p: {"status": "020", "message": "사용한도를 초과"})
    with pytest.raises(dart.DartError, match="한도"):
        dart.fetch_range("20260101", "20260131")
