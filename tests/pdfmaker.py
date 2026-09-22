"""A minimal PDF writer for tests. No dependencies.

Lays out lines of text top to bottom in Helvetica, the way a SongSelect chart
puts the title and writers at the top and the CCLI footer at the bottom.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    # The Pages object comes straight after every page's content and page
    # objects, so its number is known before they are written.
    pages_id = len(objects) + 2 * len(pages) + 1
    kids = []
    for lines in pages:
        stream_lines = ["BT", "/F1 11 Tf", "14 TL", "50 760 Td"]
        for line in lines:
            stream_lines.append(f"({_escape(line)}) Tj T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("cp1252")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, font, content)
        )
        kids.append(page)
    kid_refs = b" ".join(b"%d 0 R" % k for k in kids)
    real_pages = add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kid_refs, len(kids)))
    assert real_pages == pages_id, (real_pages, pages_id)
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % real_pages)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, xref,
    )
    return bytes(out)


def songselect_chart(title="10,000 Reasons (Bless The Lord)",
                     writers="Words and Music by Jonas Myrin and Matt Redman",
                     header="Key - G | Tempo - 73 | Time - 4/4",
                     ccli="6016351", pages=1) -> bytes:
    body = ["G D/F# Em C", "Bless the Lord O my soul", "O my soul"]
    footer = [
        f"CCLI Song # {ccli}" if ccli else "",
        "© 2011 Atlas Mountain Songs",
        "For use solely with the SongSelect Terms of Use. All rights reserved.",
        "CCLI License # 1111111",
    ]
    first = [title, writers, header, ""] + body + [""] * 30 + footer
    rest = [body + [""] * 30 + footer for _ in range(pages - 1)]
    return make_pdf([first] + rest)
