"""A small PDF writer for text reports: monospaced lines on A4 pages, no dependency.

PDF is plain text underneath: a few numbered objects (catalog, pages, one font, a content
stream per page) and a table of where each starts. Courier is one of the 14 fonts every PDF
reader has, so nothing is embedded. Text is Latin-1 (the rupee sign becomes "Rs").
"""

PAGE_W, PAGE_H = 842, 595  # A4 landscape, in points
MARGIN = 36
SIZE = 8  # points
LEADING = 10
LINES_PER_PAGE = (PAGE_H - 2 * MARGIN) // LEADING


def text_pdf(lines: list[str], title: str = "") -> bytes:
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in below, once the pages object number is known
    pages_id = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    kids = []
    for n, page in enumerate(pages, 1):
        stream = _content(page, f"{title}  page {n} of {len(pages)}" if title else "")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>"
            % (pages_id, PAGE_W, PAGE_H, content, font)
        ))  # fmt: skip
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    refs = b" ".join(b"%d 0 R" % k for k in kids)
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (refs, len(kids))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, xref)  # fmt: skip
    return bytes(out)


def _content(lines: list[str], footer: str) -> bytes:
    parts = [b"BT /F1 %d Tf %d TL %d %d Td" % (SIZE, LEADING, MARGIN, PAGE_H - MARGIN - SIZE)]
    for line in lines:
        parts.append(b"(" + _escape(line) + b") '")
    parts.append(b"ET")
    if footer:
        parts.append(
            b"BT /F1 7 Tf %d %d Td (" % (MARGIN, MARGIN // 2) + _escape(footer) + b") Tj ET"
        )
    return b"\n".join(parts)


def _escape(text: str) -> bytes:
    text = text.replace("₹", "Rs ").replace("−", "-").replace("·", "-").replace("–", "-")
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
