"""KIS 업종 코드표: 시장구분+코드 → 이름, 중분류 우선·없으면 대분류·둘 다 없으면 기타."""
from src.universe import kis_master as km


def test_parse_idxcode_and_label():
    text = "00027제조                                    \n00013전기·전자                               \n" \
           "00029IT 서비스                              \n11009제조                                    \n"
    names = km.parse_idxcode(text)
    assert names["00013"] == "전기·전자" and names["11009"] == "제조"
    assert km.sector_label({"sector_l": "0027", "sector_m": "0013"}, names, "0") == "전기·전자"
    assert km.sector_label({"sector_l": "0029", "sector_m": "0000"}, names, "0") == "IT 서비스"
    assert km.sector_label({"sector_l": "1009", "sector_m": "0000"}, names, "1") == "제조"
    assert km.sector_label({"sector_l": "0000", "sector_m": "0000"}, names, "0") == "기타"
