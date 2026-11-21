"""Loading the store as records: `loadRecords` in `scripts/omics/records.ts`.

`store.checked_records` holds each line against the layout and its provenance row and knows
nothing of what a record is. This is the whole of `loadRecords`, in the order the TypeScript
applies it:

1. each line is parsed by the schema, and it is the parsed record that is placed, ordered
   and hashed, so a line whose top-level keys are out of order is hashed as the schema
   would write it;
2. every record has a provenance row, and no row is left over;
3. every controlled value is a concept of its vocabulary;
4. every attribute is declared for its kind and of its declared type;
5. every derivation names a script and sources that are there;
6. no description is a placeholder, carries provenance, or is shared;
7. every scoped evidence concern is on a source and names records it may name;
8. `validateRecords`, over the records by ID: `validation.validate_records`.

It stops at the first validator that refuses, as `loadRecords` does. `check_command` is
`npm run records -- check`.
"""

from __future__ import annotations

from pathlib import Path

from rewire_evidence.jsonl import StoreError, read_rows
from rewire_evidence.schema import Record, SchemaError, parse_record
from rewire_evidence.store import PROVENANCE_DIR, RecordCheck, read_provenance, record_files
from rewire_evidence.validation import validate_store
from rewire_evidence.vocab import load_schemes, read_mapping, validate_vocabularies


def parsed(row: object, where: str) -> Record:
    """One row as the record the schema makes of it. `where` names the row in a refusal."""
    try:
        return parse_record(row)
    except SchemaError as error:
        raise StoreError(f"{where} is not a record: {error}") from error


def read_records(root: Path) -> list[Record]:
    """Every record of a store in the order it is filed, placed, ordered and matched to its provenance.

    Raises `StoreError` for a line that is not a record or is misplaced, out of order or
    without its provenance row, and for a provenance row with no record.
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
    return records


def load_records(root: Path, configuration: Path | None = None) -> list[Record]:
    """Every record of a store, sorted by ID, having passed every check `loadRecords` makes.

    `configuration` is the checkout whose `data/ontology/mapping.json` and `data/vocab` the
    records are held to. The TypeScript reads those from the repository it runs in, whatever
    store it is loading, so a store under another root is checked against the repository's
    vocabularies. It defaults to the store's own root. A derivation's script is read from
    the store's root, as the TypeScript reads it.

    Raises `StoreError` for a line that is not a record or is misplaced, out of order or
    without its provenance, `VocabularyError` for a vocabulary that cannot be read or a
    value outside one, `RecordError` for a fault any later validator finds, and
    `SchemaError` for a structured block zod refuses. Each stops at the first fault, as
    `loadRecords` does.
    """
    records = read_records(root)
    settings = root if configuration is None else configuration
    validate_vocabularies(
        read_mapping(settings), (record.row() for record in records), load_schemes(settings)
    )
    return validate_store(records, root)
