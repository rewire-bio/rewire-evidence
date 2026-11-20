"""Turn primary literature into checkable benchmark records.

The modules, in the order they depend on each other:

  canonical   the three serialisations and the digests built on them
  jsonl       reading and writing line files without disturbing their bytes
  store       where records and provenance live, and the logical store digests
  families    every family of store file: serialisation, order, placement
  parity      what the gates measure when they hold all of the above against a store checkout
  gates       the gates as commands, which the files under scripts/ call
"""
