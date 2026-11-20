# rewire-evidence

Turns primary literature into benchmark records, each bound to a byte span in an archived copy of its source, so a reader can check a number without trusting us.

This is the Python rebuild of the data pipeline in `rewire-benchmark-data`. That repository is already about two thirds Python by line count, but the Python is 412 one-off per-batch scripts carrying 42 copies of one helper library in 11 drifted versions, and the TypeScript lane built to replace them converted 19 batches and stopped. Everything the next stage of the work needs is Python anyway: the knowledge-graph server, the benchmark runner, and the NLP libraries the extraction depends on.

## Code here, data there

This repository holds the library, its tests and the verification gates. The records stay in `rewire-benchmark-data`, which depends on a released version of this package and keeps the run recipes that produce its data. Nothing in here holds a copy of the store.

The split works because the interface between the two is the on-disk JSONL, not any function signature, and because this code is forbidden from changing those bytes: the committed digests and layer hashes are the acceptance test. So a change here cannot require a coordinated change there. What it buys is a producer you can name. Today you cannot tell which of the 11 drifted helper versions wrote a given row; a batch produced against `rewire-evidence==0.4.2` can say so on its receipt, and the claim is checkable.

## The discipline

The old store is the test oracle, and it is big enough to be decisive. Nothing here is accepted because its unit tests pass. It is accepted when it reproduces data that already exists.

| What is being ported | Oracle | Rows | Status |
| --- | --- | --- | --- |
| Record serialisation and digests | `data/provenance/records/*.jsonl` | 93,216 | Passing |
| Object manifest and receipts | `data/archive/objects/*.jsonl` | 1,167 | Not started |
| Layer derivation | `data/omics/layers/registry.jsonl` | 2,595 | Not started |
| Locator grammar | committed coverage report | 52,278 | Not started |
| Span resolution | committed figures and TSV reports | 52,278 | Not started |
| Extractor equivalence | position-joined candidate comparison | 418,440 | Not started |

The gate runs in CI against one pinned commit of the store, not against main as it stands, so a red run has one explanation rather than two. Bumping the pin is a commit of its own.

Run it here:

```sh
uv run --python 3.12 --with-editable . python scripts/verify_store_digests.py ../rewire-benchmark-data
```

## Why byte identity matters here

A record's identity is `sha256(JSON.stringify(record))`, so the Python has to agree with JavaScript on how a number is written, not merely on what it means. JavaScript writes `0.00001` where Python's default writes `1e-05`, and that one difference breaks four records in the live store. Above 2^53 the two disagree again, in the other direction: JavaScript writes the shortest digits that round-trip and pads with zeros, Python's `int()` writes the exact binary value.

`src/rewire_evidence/canonical.py` reproduces ECMAScript `Number::toString` and the three different serialisations the store uses. They are not interchangeable, and one of them is a trap: `canonicalLine` sorts top-level keys only, while `canonical_json` sorts at every depth. Python's `json.dumps(sort_keys=True)` is the recursive one, so using it where the manifest form belongs writes valid, sorted, wrong bytes on any row holding a nested object.

## Tests

```sh
uv run --python 3.12 --with pytest --with-editable . python -m pytest
```

`tests/js-fixture.json` is output Node actually produced, committed rather than described, so the number tests are a recording of the other implementation instead of an opinion about the specification. It is what caught the 2^53 bug, which the 93,206-record oracle could not, because the store holds no value that large.

## Layout

```
src/rewire_evidence/canonical.py   serialisation, digests, shards
scripts/                           verification gates against a benchmark-data checkout
tests/                             pytest, with the Node fixture
```
