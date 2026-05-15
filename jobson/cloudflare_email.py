from __future__ import annotations

"""Cloudflare email obfuscation decoder.

When Cloudflare detects an email in HTML it replaces it with
`<span class="__cf_email__" data-cfemail="HEXSTRING">[email protected]</span>`.

The HEX string is XOR-encoded: first byte is the key, remaining bytes are the
ASCII codes of the email XOR'd with the key. Decoding is trivial.
"""


def decode_cfemail(hex_str: str) -> str:
    """Decode a Cloudflare obfuscated email. Returns "" on malformed input."""
    if not hex_str or len(hex_str) < 4 or len(hex_str) % 2 != 0:
        return ""
    try:
        key = int(hex_str[:2], 16)
        out_chars: list[str] = []
        for i in range(2, len(hex_str), 2):
            byte = int(hex_str[i:i + 2], 16) ^ key
            if byte < 32 or byte > 126:  # not printable ASCII → bail
                return ""
            out_chars.append(chr(byte))
        return "".join(out_chars)
    except (ValueError, TypeError):
        return ""
