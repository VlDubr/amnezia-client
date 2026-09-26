import io

import segno


def qr_svg(text: str) -> str:
    buf = io.BytesIO()
    # omitsize: a viewBox instead of a fixed width, so the page can scale it; light: scanners need a white
    # background even in a dark UI.
    segno.make(text, error="l", micro=False).save(buf, kind="svg", scale=4, border=2, xmldecl=False,
                                                  omitsize=True, light="#fff")
    return buf.getvalue().decode()
