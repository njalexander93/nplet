"""Check criterion 12 encodings the shared content cases do not already cover."""

from pathlib import Path

import pytest

from nplet.adapters import task_boundary as boundary

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("data", "state"),
    [
        (b"\xfe\xff" + "notes\n".encode("utf-16-be"), boundary.TEXT),
        (b"\xff\xfe\x00\x00" + "notes\n".encode("utf-32-le"), boundary.TEXT),
        # BOM-less UTF-16, an odd-length UTF-16 body and Shift JIS stay unknown.
        ("notes\n".encode("utf-16-le"), boundary.UNKNOWN),
        (b"\xff\xfe" + "notes".encode("utf-16-le") + b"n", boundary.UNKNOWN),
        ("メモ".encode("shift_jis"), boundary.UNKNOWN),
        # UTF-16 text whose bytes contain C0 controls is not text.
        (b"\xff\xfe" + "a\x01".encode("utf-16-le"), boundary.UNKNOWN),
    ],
)
def test_bom_text_and_unsupported_encodings(tmp_path: Path, data: bytes, state: str) -> None:
    """A BOM selects the codec; anything else must be strict UTF-8 to count as text.

    Args:
        tmp_path: Directory holding the classified file.
        data: Generated file content.
        state: Expected content state.
    """
    path = tmp_path / "f.png"
    path.write_bytes(data)
    assert boundary.classify(path, boundary.Budget()) == state
