# rewire-evidence

Turns primary literature into benchmark records, each bound to a byte span in an archived copy of its source, so a reader can check a number without trusting us.

This is the Python rebuild of the data pipeline in `rewire-benchmark-data`. That repository is already about two thirds Python by line count, but the Python is 412 one-off per-batch scripts carrying 42 copies of one helper library in 11 drifted versions, and the TypeScript lane built to replace them converted 19 batches and stopped. Everything the next stage of the work needs is Python anyway: the knowledge-graph server, the benchmark runner, and the NLP libraries the extraction depends on.

## Code here, data there

This repository holds the library, its tests and the verification gates. The records stay in `rewire-benchmark-data`, which depends on a released version of this package and keeps the run recipes that produce its data. Nothing in here holds a copy of the store.

The split works because the interface between the two is the on-disk JSONL, not any function signature, and because this code is forbidden from changing those bytes: the committed digests and layer hashes are the acceptance test. So a change here cannot require a coordinated change there. What it buys is a producer you can name. Today you cannot tell which of the 11 drifted helper versions wrote a given row; a batch produced against `rewire-evidence==0.4.2` can say so on its receipt, and the claim is checkable.

## The discipline

The old store is the test oracle, and it is big enough to be decisive. Nothing here is accepted because its unit tests pass. It is accepted when it reproduces data that already exists.

| Stage | What is being ported | Oracle | Rows | Status |
| --- | --- | --- | --- | --- |
| P0 | Record serialisation and digests | `data/provenance/records/*.jsonl` | 93,216 | Passing |
| P1 | Store layout, and reading and writing every family of store file | every committed line file, and the 18 logical digests of release `2026-11-20-72316db0764b` | 199,585 lines in 100 files | Passing ([notes](docs/stages/p1.md)) |
| P2 | Record schema and key order, kinds, statuses, relation rules, public records | every committed record parsed and written back as its committed line, and again with its keys moved; 299 inputs recorded from zod, 209 of them refused | 93,216 records, 138,962 links | Passing ([notes](docs/stages/p2-p3.md)) |
| P3 | Vocabulary loader and controlled values, with n3's Turtle reader ported so a scheme is read as the TypeScript reads it | the 25 SKOS schemes as n3 reads them, statement for statement, and every controlled value in the store; 921 Turtle documents recorded from n3, 784 of them refused | 441 concepts, 3,315 statements, 580,242 values | Passing ([notes](docs/stages/p2-p3.md)) |
| P4 | Attribute registry and its types, older shapes, descriptions, derivations, evidence concerns, with as much of zod as the validators use | every attribute value of the store by declared type; 8,574 values recorded from zod, each a valid block damaged in one place or more; 98 older shapes and a 42-record release recorded from `normalizeAttributes` | 766,774 values, 134,822 reasons for a missing value | Passing ([notes](docs/stages/p4-p5.md)) |
| P5 | The whole of `validateRecords`, comparison panels included, and `records check` as a command | `records -- check` over the store, with every panel as the catalogue query resolves it; 424 faults seeded into a 228-record store with what each validator did; 23 seeded into the whole store; `records descriptions` byte for byte on 16 stores | 93,216 records, 1,178 panels showing 12,919 results | Passing ([notes](docs/stages/p4-p5.md)) |
| P6 | Write path: `add`, `change`, restore on failure | replayed batches | | Not started |
| P7 | Locator grammar | committed coverage report | 52,278 | Not started |
| P8 | Report-only checks | `checks-baseline.json` and the row-level report | 10,754 | Not started |
| P9 | Object manifest and receipts, with their schemas | `data/archive/objects/*.jsonl` | 1,167 | Bytes reproduce (P1). Schemas not started |
| P10 | Layer derivation | `data/omics/layers/registry.jsonl` | 2,595 | Bytes reproduce (P1). Derivation not started |
| P11 | Span resolution | committed figures and TSV reports | 52,278 | Not started |
| P12 | Extractor equivalence | position-joined candidate comparison | 418,440 | Not started |

Every stage has to meet three things before it is accepted: a gate under `scripts/` that reproduces committed data and asserts the row count, 100% line and branch coverage of the library, and clean runs of `mypy --strict` and Ruff. All three run in CI. The gate scripts hold no logic of their own, so the coverage figure includes what they do.

The gate runs in CI against one pinned commit of the store, not against main as it stands, so a red run has one explanation rather than two. Bumping the pin is a commit of its own.

Run the gates here:

```sh
uv sync --group dev
uv run python scripts/verify_store_digests.py ../rewire-benchmark-data
uv run python scripts/verify_store_layout.py ../rewire-benchmark-data
uv run python scripts/verify_store_schema.py ../rewire-benchmark-data
uv run python scripts/verify_store_validation.py ../rewire-benchmark-data
```

The second one holds every count, and the SHA-256 of each family's files, against `scripts/expected/store-layout.json`, which names the store commit they were measured at. A checkout of any other commit, or an edited one, fails it.

The third parses every record with the schema and requires the committed line back, then does it again with each record's keys in another order, and checks every link, source ID and controlled value. It loads the store with `records.load_records` and requires the same records back in ID order. It holds what it measures against `scripts/expected/store-schema.json`, which is not written by this library: the TypeScript in the data repository records it, at the same commit.

The fourth runs every validator `npm run records -- check` applies, over every record. The store passes them all, and a validator that looked at nothing would pass too, so the gate holds what it measures against `scripts/expected/store-validation.json`, which the TypeScript records at the same commit: how many attribute values there are of each declared type, every comparison panel as the catalogue query resolves it, and the words each validator refuses a seeded fault in. Each fault is put into the whole store and has to be refused in the recorded words.

All four exit 0 when everything reproduces, 1 on a disagreement and 2 when the run could not be made: a store, a vocabulary or an expectation that cannot be read, or a report that cannot be written. No one gate holds every byte. What each catches of an edited checkout is set out in the [P2 and P3 notes](docs/stages/p2-p3.md), and CI runs all four.

`records check` is also a command, and prints the line the TypeScript prints:

```sh
uv run rewire-evidence records check ../rewire-benchmark-data
uv run rewire-evidence records descriptions --json ../rewire-benchmark-data
```

## Why byte identity matters here

A record's identity is `sha256(JSON.stringify(record))`, so the Python has to agree with JavaScript on how a number is written, not merely on what it means. JavaScript writes `0.00001` where Python's default writes `1e-05`, and that one difference breaks four records in the live store. Above 2^53 the two disagree again, in the other direction: JavaScript writes the shortest digits that round-trip and pads with zeros, Python's `int()` writes the exact binary value.

`src/rewire_evidence/canonical.py` reproduces ECMAScript `Number::toString` and the three different serialisations the store uses. They are not interchangeable, and one of them is a trap: `canonicalLine` sorts top-level keys only, while `canonical_json` sorts at every depth. Python's `json.dumps(sort_keys=True)` is the recursive one, so using it where the manifest form belongs writes valid, sorted, wrong bytes on any row holding a nested object.

The same care applies to things the store has no example of. A JavaScript object lists integer-like keys first, whatever order they were set in. An integer above 2^53 is only ever the double nearest to it. An unpaired surrogate is written as an escape. None of these appears in the store today, so the oracle cannot catch a mistake in any of them, and each is recorded from Node instead.

## Tests

```sh
uv run python -m pytest --cov --cov-branch --cov-fail-under=100
uv run mypy
uv run ruff check . && uv run ruff format --check .
```

`tests/js-fixture.json` and `tests/fixtures/js-semantics.json` are output Node actually produced, committed rather than described, so the tests are a recording of the other implementation instead of an opinion about the specification. `node scripts/fixtures/record_js_fixture.mjs` writes both, using built-ins only, and CI runs it to prove the files are what the script produces. The first fixture is what caught the 2^53 bug, which the record oracle could not, because the store holds no value that large.

`tests/fixtures/record-schema.json`, `tests/fixtures/vocabularies.json`, `tests/fixtures/turtle.json` and `scripts/expected/store-schema.json` are the same idea one level up: what the data repository's own TypeScript does, with zod as the schema and n3 as the Turtle parser. `scripts/fixtures/record_schema_fixture.mjs` writes them, taking its Turtle documents from `scripts/fixtures/turtle_cases.mjs`. `tests/fixtures/validation.json`, `scripts/expected/store-validation.json` and the attribute registry in `src/rewire_evidence/data/` are what its validators do, written by `scripts/fixtures/record_validation_fixture.mjs` from the cases in `scripts/fixtures/validation_cases.mjs`. Both recorders have to run inside a checkout of the data repository with its dependencies installed. That happens in the `fixtures` job in CI and nowhere else. The same job records `src/rewire_evidence/data/lowercase.json` with `scripts/fixtures/record_lowercase.mjs`: what `toLowerCase` does to every code point on the Node the data repository runs on, which the library reads so that lowercase does not follow the Unicode version of the Python it runs on. The job fails if a committed file is not what the TypeScript produces, and uploads what it recorded as the `schema-fixtures` artifact, which is how a changed fixture gets committed.

## Layout

```
src/rewire_evidence/canonical.py    serialisation, digests, shards, one form per JavaScript value
src/rewire_evidence/jsonl.py        line files, split on the newline only
src/rewire_evidence/store.py        where records and provenance live, logical digests, reshard
src/rewire_evidence/families.py     every family of store file: serialisation, order, placement
src/rewire_evidence/schema.py       what a record is: the schema, key order, kinds, relation rules
src/rewire_evidence/turtle.py       Turtle as n3 1.17.4 reads it: its lexer and parser, ported
src/rewire_evidence/vocab.py        the SKOS vocabularies and the controlled-value check
src/rewire_evidence/js.py           what JavaScript takes a value for: truth, trimming, numbers, equality
src/rewire_evidence/weburl.py       whether JavaScript's URL parser reads a piece of text
src/rewire_evidence/zod.py          as much of zod 4 as the validators use, issue for issue
src/rewire_evidence/attributes.py   the attribute registry and its types, and the older shapes
src/rewire_evidence/descriptions.py what a description may say
src/rewire_evidence/concerns.py     evidence concerns and what they cover
src/rewire_evidence/derivations.py  computed results, traced to their script and sources
src/rewire_evidence/blocks.py       profiles, run guides, recipes and the other structured blocks
src/rewire_evidence/panels.py       comparison panels, resolved as the catalogue query resolves them
src/rewire_evidence/validation.py   validateRecords, and the validators of loadRecords in order
src/rewire_evidence/records.py      loading a store as records: the whole of loadRecords
src/rewire_evidence/cli.py          the rewire-evidence command: records check, records descriptions
src/rewire_evidence/parity.py       what the digest and layout gates measure
src/rewire_evidence/conformance.py  what the schema gate measures
src/rewire_evidence/agreement.py    what the validation gate measures
src/rewire_evidence/gates.py        the gates as commands: arguments, report, exit status
scripts/                            one thin file per gate, the expectations, the fixture recorders
tests/                              pytest and Hypothesis, with the Node fixtures
docs/stages/                       one note per ported stage: oracle, measured result, known gaps
```
