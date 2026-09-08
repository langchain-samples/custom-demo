"""Turning a captured drawing into something a tool result can carry.

`sign_document` captures a handwritten mark and has two problems with it; the
answers live here rather than inline, because they are about images, not about
signing.

**Why a data URI and not a URL.** A document that embeds the picture has to
either carry the bytes or fetch them, and a model cannot retype 18,000 base64
characters into an `<img>` without corrupting them, while a URL makes the
document stop working the day the server does. So the app crops to the ink,
flattens the antialiasing, and shrinks until it fits an inline budget; these
helpers just check the result is usable.

**Why the size check.** A provider rejects an undersized image with a 400 that
kills the whole run, not just the picture. Found with a 1x1 test fixture that
failed AFTER the tool had already succeeded, which is the worst way to find it.
"""

from __future__ import annotations

import base64
import binascii
import os

# Below this, a provider refuses the image outright. A real pad canvas is
# hundreds of pixels wide, so this only ever catches a degenerate one.
MIN_IMAGE_EDGE = 16


def inline_budget(env_var: str, default: int = 14000) -> int:
    """Data-URI characters a server will inline, roughly 3.5k tokens by default.

    Above it the model is being asked to copy more than it reliably can. The env
    var is a parameter so a caller names its own budget rather than inheriting
    one from this module.
    """
    return int(os.getenv(env_var, str(default)))


def png_bytes(data_uri: str) -> bytes | None:
    """The PNG bytes out of a `data:image/png;base64,...` URI, or None if malformed."""
    _, _, payload = (data_uri or "").partition("base64,")
    if not payload:
        return None

    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def renderable(png: bytes) -> bool:
    """Whether a provider will accept this PNG, judged from its header.

    The dimensions live in the IHDR chunk, which is always first: bytes 16-24
    after the 8-byte signature. Anything unparseable counts as not renderable,
    because the cost of guessing wrong is a failed run.
    """
    if len(png) < 24 or png[12:16] != b"IHDR":
        return False

    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return width >= MIN_IMAGE_EDGE and height >= MIN_IMAGE_EDGE
