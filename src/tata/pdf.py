"""HTML -> PDF."""

# WeasyPrint ships no type information.
from weasyprint import HTML  # pyright: ignore[reportMissingTypeStubs]


def render_pdf(html: str) -> bytes:
    pdf = HTML(string=html).write_pdf()  # pyright: ignore[reportUnknownMemberType]
    if pdf is None:  # only when writing to a target, which we never pass
        raise RuntimeError("WeasyPrint non ha prodotto il PDF")
    return pdf
