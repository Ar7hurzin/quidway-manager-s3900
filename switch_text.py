"""Decode text returned by legacy switch consoles using common firmware encodings."""
from __future__ import annotations


def decode_switch_text(raw: bytes) -> str:
    """Prefer UTF-8, then Simplified Chinese Windows encoding used by older VRP builds."""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
