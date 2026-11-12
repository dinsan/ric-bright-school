import pytest

from app.core.text import slugify


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fractions & Decimals!", "fractions-decimals"),
        ("  Grade 4  Math ", "grade-4-math"),
        ("Café Écrit", "cafe-ecrit"),
        ("???", ""),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected
