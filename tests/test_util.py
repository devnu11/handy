import pytest

from handy.util import format_size


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1.0 MB"),
        (1024**5, "1.0 PB"),
        (1024**6, "1024.0 PB"),
    ],
)
def test_format_size(num_bytes, expected):
    assert format_size(num_bytes) == expected
