import pytest

from partitiondb.domains import normalize_hostname


@pytest.mark.parametrize(
    "source,expected",
    [
        ("EXAMPLE.COM.", "example.com"),
        ("localhost", "localhost"),
        ("bücher.example", "xn--bcher-kva.example"),
    ],
)
def test_canonical_hostname(source, expected):
    assert normalize_hostname(source) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "x..com",
        "x.com..",
        "*.example.com",
        "x.com:80",
        "https://x.com",
        "a b.com",
        "-x.com",
        "x-.com",
        "a" * 64 + ".com",
        ".",
    ],
)
def test_invalid_hostname(value):
    with pytest.raises(ValueError):
        normalize_hostname(value)
