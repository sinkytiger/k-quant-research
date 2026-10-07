"""분석 노트: 파일 이름 해석, 요약(눈금 숫자 줄 제외), 종목 찾기(다른 이름 안의 이름 제외), PDF 스캔·공개 구분."""
from pypdf import PdfWriter

from src import notes


def test_parse_name():
    assert notes.parse_name("2026-10-07_반도체 업황") == ("2026-10-07", "반도체 업황")
    assert notes.parse_name("20261007 메모") == ("2026-10-07", "메모")
    assert notes.parse_name("2026-13-40_x") == (None, "2026-13-40 x")
    assert notes.parse_name("그냥_메모") == (None, "그냥 메모")


def test_summarize_drops_numeric_lines():
    text = "0.0 2.5 5.0 7.5\n반도체 업황 메모\n0 1 2 3 4\n삼성전자 가격 점검.\n반도체 업황 메모"
    assert notes.summarize(text) == "반도체 업황 메모 삼성전자 가격 점검."


def test_find_stocks_boundaries():
    names = {"000660": "SK하이닉스", "452400": "이닉스", "005930": "삼성전자", "003550": "LG"}
    found = notes.find_stocks("삼성전자와 SK하이닉스(000660), LG 도 본다. 삼성전자", names)
    assert found[0] in ("005930", "000660") and set(found) == {"005930", "000660"}


def test_scan_public_private_and_cleanup(tmp_path):
    src, out = tmp_path / "notes", tmp_path / "out"
    (src / "public").mkdir(parents=True)
    for p in (src / "public" / "2026-10-01_공개 메모.pdf", src / "비공개.pdf"):
        w = PdfWriter()
        w.add_blank_page(200, 200)
        w.add_blank_page(200, 200)
        with open(p, "wb") as f:
            w.write(f)
    rows = notes.scan(src, out, {})
    assert [r["public"] for r in rows if r["title"] == "공개 메모"] == [True]
    pub = next(r for r in rows if r["public"])
    assert pub["date"] == "2026-10-01" and pub["pages"] == 2 and pub["summary"] == ""
    assert (out / pub["file"].split("/")[1]).exists()
    (src / "비공개.pdf").unlink()
    rows = notes.scan(src, out, {})
    assert len(rows) == 1 and not any("비공개" in f.name for f in out.iterdir())


def test_weekly_name_title_date_category():
    assert notes.parse_name("2026-10_1주차_주간리서치") == (None, "10월 1주차 주간리서치")
    assert notes.clean_title("Microsoft Word - 반도체 메모.docx") == "반도체 메모"
    assert notes.clean_title("untitled") is None
    assert notes.last_date("주간 리서치 — 2026-09-28 ~ 2026-10-02") == "2026-10-02"
    assert notes.category("public/주간 리서치/a.pdf") == "주간 리서치"
    assert notes.category("일간 리서치/a.pdf") == "일간 리서치"
    assert notes.category("a.pdf") == "기타"


def test_import_sources_copies_new_only(tmp_path):
    root, nd = tmp_path / "proj", tmp_path / "proj" / "notes"
    src = tmp_path / "src" / "2026" / "10"
    src.mkdir(parents=True)
    nd.mkdir(parents=True)
    (src / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (nd / "sources.json").write_text('{"sources": [{"from": "../src", "to": "주간 리서치"}]}', encoding="utf-8")
    assert notes.import_sources(nd, root) == 1
    assert (nd / "주간 리서치" / "a.pdf").exists()
    assert notes.import_sources(nd, root) == 0  # 이미 있으면 건너뜀
    (src / "a.pdf").unlink()
    assert notes.import_sources(nd, root) == 0 and (nd / "주간 리서치" / "a.pdf").exists()  # 원본을 지워도 남는다
