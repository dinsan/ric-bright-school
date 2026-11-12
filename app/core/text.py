import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """``"Fractions & Decimals!"`` -> ``"fractions-decimals"``. Returns ``""`` if nothing survives."""
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _NON_ALNUM.sub("-", ascii_text.lower()).strip("-")
