"""pdf-text-layer@1: a PDF through pdftotext, and its page count through pdfinfo.

This is `shared/archive/layers/pdf.ts`, `readPinnedPdfText` in `scripts/omics/extract/pdf.ts`
and `pdfPageCount` in `shared/omics/layers/registry.ts`. The bytes go to pdftotext on
standard input, so the conversion is bound to the bytes that were hashed, and the version
line pdftotext prints is written into the layer's derivation. A poppler change moves the
output SHA-256 and therefore names a new layer. It never rewrites one.

These are the only functions in the layer derivation that run another program. What that
program writes is not a function of the bytes and its version alone: the same poppler
release built on another machine was measured writing other bytes for some PDFs. See
`docs/stages/p10.md`.
"""

from __future__ import annotations

import functools
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Final

from rewire_evidence.canonical import sha256_hex
from rewire_evidence.js import SPACE_CLASS, trim
from rewire_evidence.textlayer import Layer, LayerFailure

PDF_NORMALISER: Final = "pdf-text-layer@1"
PDFTOTEXT: Final = "pdftotext"
ARGUMENTS: Final = ("-layout", "-enc", "UTF-8", "-", "-")
#: Seconds a conversion, and the question of the version, may take.
TIMEOUT: Final = 30
#: Seconds pdfinfo may take.
PAGES_TIMEOUT: Final = 120
#: Bytes pdftotext may write.
MAX_OUTPUT: Final = 64 * 1024 * 1024

_VERSION: Final = re.compile(rf"pdftotext version[{SPACE_CLASS}]+[^{SPACE_CLASS}]")
_LINE_END: Final = re.compile(r"\r?\n")
_TERMINATOR: Final = r"\n\r  "
#: `/^Pages:\s+(\d+)$/m`, whose lines end at any of JavaScript's four line terminators.
_PAGES: Final = re.compile(
    rf"(?:\A|(?<=[{_TERMINATOR}]))Pages:[{SPACE_CLASS}]+([0-9]+)(?=[{_TERMINATOR}]|\Z)"
)


def _run(arguments: list[str], data: bytes | None, timeout: int) -> subprocess.CompletedProcess[bytes]:
    """Run a program, or raise `LayerFailure` in the words Node's `spawnSync` reports an error in."""
    try:
        return subprocess.run(arguments, input=data, capture_output=True, timeout=timeout, check=False)  # noqa: S603 - the program is named here and the arguments are fixed
    except subprocess.TimeoutExpired:
        raise LayerFailure(f"spawnSync {arguments[0]} ETIMEDOUT") from None
    except FileNotFoundError:
        raise LayerFailure(f"spawnSync {arguments[0]} ENOENT") from None


@functools.cache
def has_pdftotext() -> bool:
    """Whether pdftotext can be run. Asked once for the process."""
    try:
        _run([PDFTOTEXT, "-v"], None, TIMEOUT)
    except LayerFailure:
        return False
    return True


def pdftotext_version() -> str:
    """The line pdftotext names its version in. Raises `LayerFailure` when it prints none."""
    asked = _run([PDFTOTEXT, "-v"], None, TIMEOUT)
    printed = (asked.stdout + asked.stderr).decode("utf-8", "replace")
    line = next((line for line in _LINE_END.split(printed) if _VERSION.match(line)), None)
    if asked.returncode != 0 or line is None:
        raise LayerFailure("Could not identify the pdftotext version used for extraction")
    return line


def pdf_layer(data: bytes) -> Layer:
    """The text layer of a PDF. Raises `LayerFailure` where the TypeScript throws."""
    sha = sha256_hex(data)
    converted = _run([PDFTOTEXT, *ARGUMENTS], data, TIMEOUT)
    if max(len(converted.stdout), len(converted.stderr)) > MAX_OUTPUT:
        raise LayerFailure(f"spawnSync {PDFTOTEXT} ENOBUFS")
    if converted.returncode != 0:
        raise LayerFailure(f"pdftotext failed: {trim(converted.stderr.decode('utf-8', 'replace'))}")
    version = pdftotext_version()
    try:
        text = converted.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise LayerFailure(f"pdftotext output for {sha} does not round trip through UTF-8") from None
    return Layer(
        normaliser=PDF_NORMALISER,
        tool=PDFTOTEXT,
        tool_version=version,
        derivation=f"{PDF_NORMALISER} {PDFTOTEXT} {' '.join(ARGUMENTS)} ({version})",
        input_sha256=sha,
        output_sha256=sha256_hex(converted.stdout),
        content=converted.stdout,
        characters=len(text),
        identity=False,
    )


def pdf_page_count(data: bytes) -> int | None:
    """Pages as pdfinfo reports them, or `None` when it cannot read the file."""
    with tempfile.TemporaryDirectory(prefix="layer-pages-") as folder:
        file = Path(folder) / "object.pdf"
        file.write_bytes(data)
        try:
            report = _run(["pdfinfo", str(file)], None, PAGES_TIMEOUT)
        except LayerFailure:
            return None
    if report.returncode != 0:
        return None
    pages = _PAGES.search(report.stdout.decode("utf-8", "replace"))
    return int(pages.group(1)) if pages else None
