"""분석 노트: notes/ 폴더의 PDF 를 대시보드 연구 탭에 올린다.

- notes/*.pdf, notes/**/*.pdf 를 읽는다. notes/public/ 아래 것만 공개 저장소로 나간다(나머지는 비공개).
- 파일 이름이 `2026-10-07_제목.pdf` 이면 날짜·제목을 거기서, 아니면 PDF 정보(Title)·파일 수정일을 쓴다.
- 첫 쪽 글자로 요약 몇 줄과 본문에 나온 종목(종목명·코드)을 뽑는다. 스캔 PDF 처럼 글자가 없으면 비워 둔다.
- 대시보드에서 바로 열 수 있게 outputs/notes/ 로 복사하고, PyMuPDF 가 있으면 첫 쪽 미리보기 PNG 를 만든다.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"^(\d{4})[-.]?(\d{2})[-.]?(\d{2})[ _\-]+(.+)$")
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def parse_name(stem: str) -> tuple[str | None, str]:
    """'2026-10-07_반도체 업황' → ('2026-10-07', '반도체 업황'). 날짜가 없으면 (None, 이름)."""
    m = NAME_RE.match(stem.strip())
    if not m:
        return None, stem.replace("_", " ").strip()
    y, mo, d, rest = m.groups()
    try:
        datetime(int(y), int(mo), int(d))
    except ValueError:
        return None, stem.replace("_", " ").strip()
    return f"{y}-{mo}-{d}", rest.replace("_", " ").strip()


def summarize(text: str, n: int = 220) -> str:
    """글자가 있는 줄만 이어 붙인다 (차트 눈금처럼 숫자·기호뿐인 줄은 뺀다)."""
    lines, seen = [], set()
    for line in (text or "").splitlines():
        t = re.sub(r"\s+", " ", line).strip()
        letters = len(re.findall(r"[가-힣A-Za-z]", t))
        if letters < 4 or letters < 0.4 * len(t.replace(" ", "")) or t in seen:
            continue
        seen.add(t)
        lines.append(t)
    t = " ".join(lines)
    return t if len(t) <= n else t[:n].rstrip() + "…"


def find_stocks(text: str, names: dict[str, str], top: int = 6) -> list[str]:
    """본문에 나온 종목코드 → 많이 나온 순. 종목명은 3글자 이상만 센다(‘LG’ 같은 짧은 이름은 오탐이 많다)."""
    if not text:
        return []
    cnt: Counter = Counter()
    for c in CODE_RE.findall(text):
        if c in names:
            cnt[c] += 1
    for code, name in names.items():
        if len(name) >= 3 and name in text:
            # 이름 앞에 다른 글자가 붙어 있으면 다른 회사 이름의 일부다 ('SK하이닉스' 안의 '이닉스'). 뒤는 조사가 붙으므로 보지 않는다
            k = len(re.findall(r"(?<![가-힣A-Za-z0-9])" + re.escape(name), text))
            if k:
                cnt[code] += k
    return [c for c, _ in cnt.most_common(top)]


def note_id(rel: str) -> str:
    stem = re.sub(r"[^\w가-힣\-]+", "_", Path(rel).stem)[:40].strip("_") or "note"
    return f"{stem}-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:6]}"


def read_pdf(path: Path, max_pages: int = 3) -> dict:
    """{'pages', 'title', 'text'} — 앞 max_pages 쪽 글자. 읽기 실패는 빈 값."""
    try:
        from pypdf import PdfReader

        r = PdfReader(str(path))
        meta_title = (r.metadata.title if r.metadata else None) or None
        text = "\n".join((p.extract_text() or "") for p in r.pages[:max_pages])
        return {"pages": len(r.pages), "title": meta_title, "text": text}
    except Exception:  # noqa: BLE001
        return {"pages": None, "title": None, "text": ""}


def thumbnail(pdf: Path, png: Path, width: int = 360) -> bool:
    """첫 쪽 미리보기. PyMuPDF 가 없거나 실패하면 False (화면은 아이콘으로 대신한다)."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # 예전 이름
        except ImportError:
            return False
    try:
        with pymupdf.open(str(pdf)) as doc:
            page = doc[0]
            zoom = width / page.rect.width
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(str(png))
        return True
    except Exception:  # noqa: BLE001
        return False


def _fresh(src: Path, dst: Path) -> bool:
    return dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime and dst.stat().st_size > 0


def scan(notes_dir: Path, out_dir: Path, names: dict[str, str]) -> list[dict]:
    """notes_dir 의 PDF → 화면용 목록 (최신 날짜 먼저). PDF·미리보기는 out_dir 로 복사·생성한다."""
    if not notes_dir.exists():
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, keep = [], set()
    for pdf in sorted(notes_dir.rglob("*.pdf")):
        rel = pdf.relative_to(notes_dir).as_posix()
        nid = note_id(rel)
        date, title = parse_name(pdf.stem)
        info = read_pdf(pdf)
        first = read_pdf(pdf, max_pages=1)["text"] if info["pages"] else ""
        dst, png = out_dir / f"{nid}.pdf", out_dir / f"{nid}.png"
        if not _fresh(pdf, dst):
            shutil.copy2(pdf, dst)
        has_png = _fresh(pdf, png) or thumbnail(pdf, png)
        keep.update({dst.name, png.name})
        rows.append({
            "id": nid, "file": f"notes/{dst.name}", "thumb": f"notes/{png.name}" if has_png else None,
            "title": title if date or not info["title"] else info["title"],
            "date": date or datetime.fromtimestamp(pdf.stat().st_mtime).strftime("%Y-%m-%d"),
            "pages": info["pages"], "kb": round(pdf.stat().st_size / 1024),
            "public": rel.split("/")[0] == "public", "folder": str(Path(rel).parent.as_posix()),
            "summary": summarize(first), "stocks": find_stocks(info["text"], names),
        })
    for f in out_dir.iterdir():  # 지운 노트의 사본 정리
        if f.is_file() and f.name not in keep:
            f.unlink()
    return sorted(rows, key=lambda r: (r["date"], r["title"]), reverse=True)
