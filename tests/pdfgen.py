"""Build a tiny one-page text PDF in memory, so tests don't need downloaded files."""


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str]) -> bytes:
    content = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({_esc(line)}) '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def guidelines_pdf(deadline: str = "Friday, May 15, 2026", fy: str = "2027") -> bytes:
    return make_pdf(
        [
            "U.S. Senate Committee on Appropriations",
            "Subcommittee on the Legislative Branch",
            "Outside Witness Testimony Instructions",
            f"Fiscal Year {fy} Appropriations",
            "Format: Testimony should be a maximum of four (4) pages.",
            "Delivery: Please e-mail testimony to legislativebranch@appro.senate.gov with the subject",
            f'line "FY{fy[2:]} LegisBranch OWT". We can only accept testimony in Microsoft Word or Word',
            "Perfect formats. DO NOT SEND PDF FILES.",
            f"Deadline: All material must be received no later than close of business, {deadline}.",
        ]
    )
