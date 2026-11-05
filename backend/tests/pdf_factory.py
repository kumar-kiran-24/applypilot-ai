"""Build tiny, valid PDFs for resume-upload tests.

The PDFs contain real text objects so ``pypdf`` extracts the lines exactly as a
text-based resume would, without adding a PDF-generation dependency.
"""

from __future__ import annotations

from collections.abc import Iterable


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(lines: Iterable[str], *, page_text: str | None = None) -> bytes:
    """Return a single-page PDF whose text lines are ``lines``."""
    body_lines = list(lines)
    content = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
    for line in body_lines:
        content.append(f"({_escape(line)}) Tj")
        content.append("T*")
    content.append("ET")
    stream_text = page_text if page_text is not None else "\n".join(content)
    stream = stream_text.encode("latin-1", "replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(out)


def scanned_pdf() -> bytes:
    """A PDF with no text operators at all (as a scanned page would be)."""
    return make_pdf([], page_text="q 612 0 0 792 0 0 cm /Im0 Do Q")


RESUME_LINES: tuple[str, ...] = (
    "Ada Lovelace",
    "ada@example.com | +1 555 010 0199",
    "London, UK",
    "Professional Summary",
    "Backend engineer building reliable data platforms with Python and FastAPI.",
    "Technical Skills",
    "Python, FastAPI, PostgreSQL, Docker, AWS, TypeScript, React",
    "Work Experience",
    "Senior Backend Engineer | Analytical Engines Ltd | London",
    "2021 - Present",
    "Designed asynchronous ingestion pipelines handling 2M events daily.",
    "Mentored three engineers and introduced contract testing.",
    "Software Engineer | Babbage Systems | Remote",
    "2018 - 2021",
    "Built REST APIs with Django and PostgreSQL.",
    "Education",
    "University of London",
    "BSc Computer Science",
    "2014 - 2018",
    "Projects",
    "ApplyPilot: Python job application agent using FastAPI and PostgreSQL",
    "https://github.com/ada/applypilot",
    "Certifications",
    "AWS Certified Solutions Architect",
    "Certified Kubernetes Administrator",
)
