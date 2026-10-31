"""Classify one side of a file effect as text, binary, unknown or absent.

Inspection reads only regular files, within a byte budget shared by one call. The
binary checks are signature and structure heuristics, not format validators.
"""

from __future__ import annotations

import os
import re
import stat
import zlib
from dataclasses import dataclass
from pathlib import Path

from nplet.adapters import repository_location

# Content states of one side of an edit effect.
TEXT, BINARY, UNKNOWN, ABSENT = "text", "binary", "unknown", "absent"
# Largest file whose whole content can be text evidence.
TEXT_LIMIT = 1 << 20
# Most bytes one call inspects across every effect side.
CALL_LIMIT = 16 << 20
# Binary evidence reads the first 64 KiB and the last 65,557 bytes, the largest ZIP
# end-of-central-directory span (22-byte record plus a 65,535-byte comment).
HEAD, TAIL = 64 << 10, 65_557


@dataclass
class Budget:
    """Bytes one call may still inspect across every effect side.

    Attributes:
        remaining: Uncharged bytes; negative once a charge exceeded the limit.
    """

    remaining: int = CALL_LIMIT


def text_evidence(data: bytes) -> bool:
    """Report whether whole file content is plausibly text.

    Args:
        data: The complete content.

    Returns:
        Whether the content is empty, strict UTF-8 with an optional BOM, or strict
        UTF-16/UTF-32 with a BOM, and decodes without NUL or C0 controls other than
        tab, LF, CR and FF.
    """
    # Empty content is text; otherwise pick the codec by BOM, UTF-32LE before UTF-16LE.
    if not data:
        return True
    boms = (
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
        (b"\xef\xbb\xbf", "utf-8"),
        (b"", "utf-8"),
    )
    bom, codec = next(pair for pair in boms if data.startswith(pair[0]))
    # Strict decoding rejects invalid sequences and unpaired surrogates.
    try:
        text = data[len(bom) :].decode(codec)
    except UnicodeDecodeError:
        return False
    return re.search("[\x00-\x08\x0b\x0e-\x1f]", text) is None


def _zip_directory(tail: bytes) -> bool:
    """Find a complete ZIP end-of-central-directory record ending the file.

    Args:
        tail: The last ``TAIL`` bytes or fewer, ending at the end of the file.

    Returns:
        Whether some signature starts a 22-byte record whose declared comment runs
        exactly to the end of the file.
    """
    at = tail.rfind(b"PK\x05\x06")
    while at >= 0:
        if at + 22 + int.from_bytes(tail[at + 20 : at + 22], "little") == len(tail):
            return True
        at = tail.rfind(b"PK\x05\x06", 0, at)
    return False


def binary_evidence(head: bytes, tail: bytes, size: int) -> bool:
    """Report whether a file's edges match one supported binary format heuristic.

    Args:
        head: The first ``HEAD`` bytes or fewer.
        tail: The last ``TAIL`` bytes or fewer.
        size: The file size.

    Returns:
        Whether PNG, JPEG, GIF, WOFF/WOFF2, ZIP or PDF framing is present.
    """
    # PNG needs its IHDR chunk with a matching CRC over type and data.
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return (
            head[8:16] == b"\x00\x00\x00\x0dIHDR"
            and len(head) >= 33
            and zlib.crc32(head[12:29]) == int.from_bytes(head[29:33], "big")
        )
    # The other formats pair a leading signature with a trailing marker or declared size.
    return (
        (head.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9"))
        or (head.startswith((b"GIF87a", b"GIF89a")) and tail.endswith(b";"))
        or (head.startswith((b"wOFF", b"wOF2")) and int.from_bytes(head[8:12], "big") == size)
        or (head.startswith(b"PK\x03\x04") and len(head) >= 30 and _zip_directory(tail))
        or (head.startswith((b"%PDF-1.", b"%PDF-2.")) and b"%%EOF" in tail[-1024:])
    )


def classify(path: Path, budget: Budget) -> str:
    """Classify one existing path's content without opening special files.

    Args:
        path: Link-resolved path to inspect; a link found here is not followed.
        budget: The call's inspection budget, charged before reading.

    Returns:
        ``absent`` for a missing path; ``text`` or ``binary`` when only that evidence
        holds; ``unknown`` for both or neither, a non-regular or unreadable file, or an
        exhausted budget.
    """
    # Only a regular file is ever opened; a missing path is absent.
    try:
        info = os.lstat(path)
    except OSError as error:
        return ABSENT if error.errno in repository_location.MISSING else UNKNOWN
    if not stat.S_ISREG(info.st_mode):
        return UNKNOWN
    # Charge the bytes this side reads; once the call limit is passed, stop reading.
    size = info.st_size
    budget.remaining -= size if size <= TEXT_LIMIT else HEAD + TAIL
    if budget.remaining < 0:
        return UNKNOWN
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError:
        return UNKNOWN
    with os.fdopen(fd, "rb") as file:
        try:
            # Recheck the opened file, then read it whole or read only its edges.
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return UNKNOWN
            if size <= TEXT_LIMIT:
                data = file.read(size + 1)
                # A file that changed size since stat has no reliable evidence.
                if len(data) != size:
                    return UNKNOWN
                head, tail, text = data[:HEAD], data[-TAIL:], text_evidence(data)
            else:
                head = file.read(HEAD)
                file.seek(size - TAIL)
                tail, text = file.read(TAIL), False
        except OSError:
            return UNKNOWN
    # Exactly one kind of evidence decides the state.
    binary = binary_evidence(head, tail, size)
    return TEXT if text and not binary else BINARY if binary and not text else UNKNOWN
