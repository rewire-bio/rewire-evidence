"""Turn primary literature into checkable benchmark records.

The modules, in the order they depend on each other:

  canonical   the three serialisations and the digests built on them
  jsonl       reading and writing line files without disturbing their bytes
  store       where records and provenance live, and the logical store digests
  families    every family of store file: serialisation, order, placement
  batch       shared extraction, review receipt, and store write helpers for batches
  parity      what the gates measure when they hold all of the above against a store checkout
  batchparity per-wave batch inventory and isolated script reproduction checks
  gates       the gates as commands, which the files under scripts/ call
"""

from __future__ import annotations

from typing import Final

#: Installed distribution version stamped on batch review receipts.
__version__: Final[str] = "0.1.0"

__all__ = ["__version__"]
