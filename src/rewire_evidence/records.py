"""Loading the store as records: `loadRecords` in `scripts/omics/records.ts`, as far as ported.

`store.checked_records` holds each line against the layout and its provenance row and knows
nothing of what a record is. This adds what the later stages know, in the order the
TypeScript applies it:

1. each line is parsed by the schema, and it is the parsed record that is placed, ordered
   and hashed, so a line whose top-level keys are out of order is hashed as the schema
   would write it;
2. every controlled value is a concept of its vocabulary;
3. no two records share an ID, every source ID names a source, and every link points at a
   record its relation may reach.

Still to come, and run by the TypeScript between and after those: the attribute registry,
derivations, descriptions, evidence concerns, and the rest of `validateRecords`.
"""

from __future__ import annotations

from pathlib import Path

from rewire_evidence.canonical import utf16_key
from rewire_evidence.jsonl import StoreError, read_rows
from rewire_evidence.schema import Record, SchemaError, parse_record, validate_references
from rewire_evidence.store import PROVENANCE_DIR, RecordCheck, read_provenance, record_files
from rewire_evidence.vocab import load_schemes, read_mapping, validate_vocabularies


def parsed(row: object, where: str) -> Record:
    """One row as the record the schema makes of it. `where` names the row in a refusal."""
    try:
        return parse_record(row)
    except SchemaError as error:
        raise StoreError(f"{where} is not a record: {error}") from error


def load_records(root: Path, configuration: Path | None = None) -> list[Record]:
    """Every record of a store, sorted by ID, having passed every check ported so far.

    `configuration` is the checkout whose `data/ontology/mapping.json` and `data/vocab` the
    records are held to. The TypeScript reads those from the repository it runs in, whatever
    store it is loading, so a store under another root is checked against the repository's
    vocabularies. It defaults to the store's own root.

    Raises `StoreError` for a line that is not a record or is misplaced, out of order or
    without its provenance, `VocabularyError` for a vocabulary that cannot be read or a
    value outside one, and `RecordError` for a fault in IDs, sources or links. Each stops
    at the first fault, as `loadRecords` does.
    """
    held = RecordCheck(root, read_provenance(root))
    records: list[Record] = []
    for file in record_files(root):
        previous = b""
        for number, row in enumerate(read_rows(file), 1):
            where = f"{file}:{number}"
            record = parsed(row, where)
            previous = held.check(record.row(), where, file, previous)
            records.append(record)
    if len(held.provenance) != len(records):
        raise StoreError(
            f"{PROVENANCE_DIR} lists {len(held.provenance)} records; the store holds {len(records)}"
        )
    settings = root if configuration is None else configuration
    validate_vocabularies(
        read_mapping(settings), (record.row() for record in records), load_schemes(settings)
    )
    records.sort(key=lambda record: utf16_key(record.id))
    validate_references(records)
    return records
