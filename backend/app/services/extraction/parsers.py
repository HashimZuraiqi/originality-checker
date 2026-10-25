"""Format-specific text extraction. These functions run inside the sandboxed
child process (see sandbox.py); they must not be called on untrusted input
in the API process directly."""

import io
import re
import zipfile
from collections import Counter

MAX_ZIP_MEMBERS = 5_000
MAX_ZIP_UNCOMPRESSED = 250 * 1024 * 1024
MAX_ZIP_RATIO = 200


class ExtractionError(Exception):
    """A user-facing problem with the uploaded file."""


def detect_type(filename: str, data: bytes) -> str:
    """Decide the real file type from magic bytes, not the extension alone."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if data.startswith(b"%PDF-"):
        if ext not in ("pdf", ""):
            raise ExtractionError("The file content is a PDF but the name says otherwise. Rename it to .pdf.")
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        if ext == "docx":
            return "docx"
        raise ExtractionError("Unsupported archive type. Upload a .docx, .pdf or .txt file.")
    if ext in ("txt", "md", "text", ""):
        return "txt"
    if ext == "doc":
        raise ExtractionError("Old .doc files are not supported. Save the document as .docx or PDF and upload again.")
    raise ExtractionError("Unsupported file type. Upload a PDF, Word (.docx) or plain-text file.")


def extract_txt(data: bytes) -> dict:
    if b"\x00" in data[:8192]:
        raise ExtractionError("This file looks binary, not plain text.")
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                raise UnicodeDecodeError(encoding, data, 0, 1, "no BOM")
            return {"text": text, "pages": None, "warnings": []}
        except UnicodeDecodeError:
            continue
    return {
        "text": data.decode("cp1252", errors="replace"),
        "pages": None,
        "warnings": ["The file was not UTF-8; it was decoded as Windows-1252 and some characters may be wrong."],
    }


def _check_zip(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ExtractionError("The Word file is damaged and cannot be opened.") from e
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ExtractionError("The Word file has an abnormal structure and was rejected.")
    total = sum(i.file_size for i in infos)
    compressed = sum(i.compress_size for i in infos) or 1
    if total > MAX_ZIP_UNCOMPRESSED or total / compressed > MAX_ZIP_RATIO:
        raise ExtractionError("The Word file expands to an unsafe size and was rejected.")
    if "word/document.xml" not in zf.namelist():
        raise ExtractionError("This .docx file does not contain a Word document.")
    return zf


def extract_docx(data: bytes) -> dict:
    _check_zip(data)
    import docx  # python-docx; its lxml parser does not resolve external entities

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 - library raises many types
        raise ExtractionError("The Word file could not be read. It may be damaged or password-protected.") from e

    parts: list[str] = []
    body = document.element.body
    # Walk the body in order so tables appear where they are in the document.
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in child.iter() if t.tag.endswith("}t"))
            parts.append(text)
        elif tag == "tbl":
            for row in child.iter():
                if row.tag.endswith("}tr"):
                    cells = []
                    for cell in row.iterchildren():
                        if cell.tag.endswith("}tc"):
                            cells.append(" ".join("".join(t.text or "" for t in p.iter() if t.tag.endswith("}t")) for p in cell.iter() if p.tag.endswith("}p")).strip())
                    parts.append(" | ".join(c for c in cells if c))
    # Footnotes/headers are intentionally skipped: they are not part of the
    # body text a reader evaluates.
    text = "\n\n".join(p.strip() for p in parts if p.strip())
    return {"text": text, "pages": None, "warnings": []}


def _clean_pdf_pages(pages: list[str]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    page_lines = [[ln.strip() for ln in p.splitlines()] for p in pages]

    # Lines repeated in the top/bottom margin of most pages are running
    # headers/footers. Only the first and last two non-empty lines of a page
    # are candidates, so similar body lines are never dropped.
    def margin_indexes(lines: list[str]) -> set[int]:
        idx = [i for i, ln in enumerate(lines) if ln]
        return set(idx[:2] + idx[-2:])

    counts: Counter[str] = Counter()
    for lines in page_lines:
        for key in {re.sub(r"\d+", "#", lines[i]) for i in margin_indexes(lines)}:
            counts[key] += 1
    threshold = max(3, int(len(pages) * 0.5))
    repeated = {k for k, c in counts.items() if c >= threshold} if len(pages) >= 4 else set()
    removed_any = False

    paragraphs: list[str] = []
    current = ""
    for lines in page_lines:
        margins = margin_indexes(lines)
        for i, ln in enumerate(lines):
            if not ln:
                if current:
                    paragraphs.append(current)
                    current = ""
                continue
            if i in margins and (
                re.sub(r"\d+", "#", ln) in repeated
                or re.fullmatch(r"(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?", ln, re.I)
            ):
                removed_any = True
                continue
            if current.endswith("-") and ln[:1].islower():
                current = current[:-1] + ln  # de-hyphenate a word broken across lines
            elif current:
                # Short line ending a sentence followed by a capitalised line
                # is most likely a paragraph break inside the page.
                if re.search(r"[.!?:]$", current) and len(current.split("\n")[-1]) < 60 and ln[:1].isupper():
                    paragraphs.append(current)
                    current = ln
                else:
                    current += " " + ln
            else:
                current = ln
    if current:
        paragraphs.append(current)
    if removed_any:
        warnings.append("Page numbers and repeated headers/footers were removed.")
    return "\n\n".join(paragraphs), warnings


def extract_pdf(data: bytes, max_pages: int) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except (PdfReadError, ValueError, OSError) as e:
        raise ExtractionError("The PDF is damaged and cannot be opened.") from e
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ExtractionError("The PDF is password-protected. Remove the password and upload again.")
        except Exception as e:  # noqa: BLE001
            raise ExtractionError("The PDF is password-protected. Remove the password and upload again.") from e
    n = len(reader.pages)
    warnings: list[str] = []
    if n > max_pages:
        warnings.append(f"Only the first {max_pages} of {n} pages were analysed.")
    pages: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page should not fail the document
            pages.append("")
            warnings.append("Some pages could not be read.")
    text, clean_warnings = _clean_pdf_pages(pages)
    warnings.extend(clean_warnings)
    if len(text.split()) < 20:
        raise ExtractionError(
            "No readable text was found in this PDF. It is probably a scanned image; "
            "OCR is not supported, so please upload a text-based PDF or the original Word file."
        )
    return {"text": text, "pages": n, "warnings": sorted(set(warnings), key=warnings.index)}
