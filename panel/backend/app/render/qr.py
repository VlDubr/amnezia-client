import io

import segno


def qr_svg(text: str) -> str:
    buf = io.BytesIO()
    segno.make(text, error="l", micro=False).save(buf, kind="svg", scale=4, border=2, xmldecl=False)
    return buf.getvalue().decode()
