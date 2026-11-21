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
| P6 | Write path: `records add`, `records change` by ID and from a changes file, each undone if the check that follows it fails | 12 merged pull requests replayed from the data repository's history against their first parent, every store file held to the merge commit's by git blob name; three changes files the TypeScript applied to the pinned store, 66 records between them; 117 writes recorded from `addBatch`, `recordChanges` and `applyChanges`, 81 of them refused | 2,601 records added, 1,283 changes recorded, 1,185 files | Passing ([notes](docs/stages/p6.md)) |
| P7 | Locator grammar, version 2 and the version 1 pattern, with the coverage report and the `locator` commands, and JavaScript's patterns run by sets of characters recorded from Node | every stored locator as `shared/omics/locator.ts` reads it, one recorded line each; the committed coverage report byte for byte; the 14 `value-check.tsv` files row for row; 2,336 locators the store has no like of, recorded from the TypeScript | 52,278 locators, 2,432 value-check rows | Passing ([notes](docs/stages/p7.md)) |
| P8 | The checks pack: seven report-only checks, `parseCell`, the baseline and the ratchet on it, and the `checks` commands; the HTML and JATS table reader of `tables.ts` | the committed report of the first run row for row and its baseline file byte for byte, from the store at the commit the baseline names; the TypeScript's run at the pinned commit, with the anchor and column of every locator and its reading of every printed value; 398 cells, 19 small stores and 18 baselines recorded from the TypeScript; every table the TypeScript reads out of the HTML and XML artifacts the pinned commit holds, one recorded line each | 10,754 findings at each commit, 52,278 locators, 26,130 printed values; 245 tables and 17,307 cells in 97 artifacts | Passing ([notes](docs/stages/p8.md)) |
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
uv run python scripts/verify_store_writes.py ../rewire-benchmark-data-history
uv run python scripts/verify_store_locators.py ../rewire-benchmark-data
uv run python scripts/verify_store_checks.py ../rewire-benchmark-data ../rewire-benchmark-data-first-run
uv run python scripts/verify_store_tables.py ../rewire-benchmark-data
```

The first four and the sixth read a checkout. The fifth reads history and no checkout, so a clone without file contents is enough, and it fetches the blobs of the stores it lays out:

```sh
git clone --filter=blob:none --no-checkout https://github.com/rewire-bio/rewire-benchmark-data.git ../rewire-benchmark-data-history
```

The second one holds every count, and the SHA-256 of each family's files, against `scripts/expected/store-layout.json`, which names the store commit they were measured at. A checkout of any other commit, or an edited one, fails it.

The third parses every record with the schema and requires the committed line back, then does it again with each record's keys in another order, and checks every link, source ID and controlled value. It loads the store with `records.load_records` and requires the same records back in ID order. It holds what it measures against `scripts/expected/store-schema.json`, which is not written by this library: the TypeScript in the data repository records it, at the same commit.

The fourth runs every validator `npm run records -- check` applies, over every record. The store passes them all, and a validator that looked at nothing would pass too, so the gate holds what it measures against `scripts/expected/store-validation.json`, which the TypeScript records at the same commit: how many attribute values there are of each declared type, every comparison panel as the catalogue query resolves it, and the words each validator refuses a seeded fault in. Each fault is put into the whole store and has to be refused in the recorded words.

The fifth replays merged pull requests. For each case in `scripts/expected/store-writes.json` it lays the store out in a scratch folder as the merge's first parent holds it, runs the commands the pull request ran (`records add` with the batch file the merge holds, `records change` with the IDs, review and date its provenance rows record) and requires every file under `data/entities`, `data/evidence` and `data/provenance` to be the merge commit's, compared by the name git gives each blob. It writes nothing to the clone. Nothing records the commands themselves, so `scripts/fixtures/derive_write_cases.py` works them out from the provenance rows each merge changed. CI works the cases out again from the clone and requires the committed file. No merged pull request has used a changes file, so three more cases, in `scripts/expected/store-recorded-writes.json`, are writes the TypeScript made in CI on the pinned store, each with the blob name of every file it left: the data repository's own reviewer fixture, added and applied, and two changes files that `scripts/fixtures/store_changes.mjs` writes from the store, kept in `scripts/expected/changes/`. One changes the first record of every record file, and the other three records, one of which already had changes recorded and one of which has its attributes in another order.

The sixth reads the locator of every result with the grammar and writes one line for each: the record's ID, the class, the coverage, the whole parse and the text it renders back to. Every line has to be the one the TypeScript wrote for that locator at the same commit, which is `scripts/expected/store-locators.jsonl.gz`, 52,278 lines recorded in CI by `scripts/fixtures/record_locator_fixture.mjs`. The store itself commits only a coverage report of twelve rows, and totals cannot show that two parsers agree on what a locator says. The gate also requires that report, as this library writes it, to be the bytes of the one the data repository commits, and the pattern of grammar version 1 to accept exactly the rows of the 14 `value-check.tsv` files that `check-values` did not report as `locator_not_parsed`.

The seventh runs the seven report-only checks twice. The data repository commits the report of the pack's first run and the baseline pinned from it, both measured on the store at an older commit than the one that holds them, so the gate takes a second checkout, of the commit the baseline names, and reads the store from there. Its report has to be the committed one row for row and its baseline file the committed one byte for byte. Then it runs the pack over the pinned checkout and requires every row the TypeScript wrote there, the anchor and column of every locator, and the reading of every printed value, which are `scripts/expected/store-checks.json` and the three files beside it, recorded in CI by `scripts/fixtures/record_checks_fixture.mjs`. The second checkout needs only the store and the vocabularies:

```sh
git clone --filter=blob:none --no-checkout https://github.com/rewire-bio/rewire-benchmark-data.git ../rewire-benchmark-data-first-run
git -C ../rewire-benchmark-data-first-run sparse-checkout set data/entities data/evidence data/vocab
git -C ../rewire-benchmark-data-first-run checkout 991a09ef6b6b417ff51d265bd87d862f49d4dbe3
```

The eighth reads every HTML and XML artifact the checkout commits under `data/` and `docs/`, the files named `.html`, `.html.gz` or `.xml.gz`, into tables. Nothing the data repository commits says what its table reader reads, so the oracle is recorded: `scripts/fixtures/record_tables_fixture.mjs` runs `scripts/omics/extract/tables.ts` over the same files in CI and writes a line for each file and a line for each table, with the caption, the rows and cells counted, and the SHA-256 of the rows, of the rows with small capitals joined and of the headed blocks. The gate requires each line, the counts, and that `tables.ts` in the checkout is the file the recording was made from.

All eight exit 0 when everything reproduces, 1 on a disagreement and 2 when the run could not be made: a store, a vocabulary, a history or an expectation that cannot be read, or a report that cannot be written. No one gate holds every byte. What each catches of an edited checkout is set out in the [P2 and P3 notes](docs/stages/p2-p3.md), and CI runs all seven.

`records check` is also a command, and prints the line the TypeScript prints:

```sh
uv run rewire-evidence records check ../rewire-benchmark-data
uv run rewire-evidence records descriptions --json ../rewire-benchmark-data
```

The locator grammar has two commands, which are `npm run locator -- coverage` and `-- parse`. The first prints the coverage table and writes it under `maintenance/reports/` in the checkout, or where `--out` says:

```sh
uv run rewire-evidence locator coverage --root ../rewire-benchmark-data --out locator-coverage.tsv
uv run rewire-evidence locator parse "Table S18, row 'Bacformer', column 'Binary AUPRC'"
```

The checks pack has two, which are `npm run checks -- report` and `-- baseline`. The first runs the seven checks, writes one row for each finding to `maintenance/reports/checks-<date>.tsv` in the checkout, or where `--out` says, prints each check's counts and says how they stand against `data/omics/checks-baseline.json`. The second writes the report and the baseline file itself:

```sh
uv run rewire-evidence checks report --root ../rewire-benchmark-data --out checks.tsv
uv run rewire-evidence checks baseline --root ../rewire-benchmark-data --date 2026-11-17 --commit 991a09ef6b6b417ff51d265bd87d862f49d4dbe3
```

So are the commands that write. Each prints the line the TypeScript prints, and with `--dry-run` writes nothing and prints the lines each store file would lose and gain:

```sh
uv run rewire-evidence records add --root ../rewire-benchmark-data --dry-run batch.jsonl data/omics/a-batch
uv run rewire-evidence records change --root ../rewire-benchmark-data docs/reviews/a-review.md data/omics/a-batch an-id another-id
uv run rewire-evidence records change --root ../rewire-benchmark-data docs/reviews/a-review.md data/omics/a-batch --changes changes.jsonl
```

Without `--dry-run` they write every store file as the TypeScript writes it and then hold what they wrote to the SHACL shapes, through the data repository's own `npm run kg -- subset` and `npm run kg:build -- --shapes-only`, which are not ported yet. So `--root` has to be a checkout of the data repository with its dependencies installed. If the batch, the change or the shapes are refused, every store file is put back as it was, including any the shapes commands touched, a store file that was not there is removed, and the command exits 1.

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

`tests/js-fixture.json` and `tests/fixtures/js-semantics.json` are output Node actually produced, committed rather than described, so the tests are a recording of the other implementation instead of an opinion about the specification. `node scripts/fixtures/record_js_fixture.mjs` writes both, using built-ins only, and CI runs it to prove the files are what the script produces. `tests/fixtures/argv.json` is how Node reads a command-line argument that is not UTF-8, written by `node scripts/fixtures/record_argv_fixture.mjs` and held the same way: `records change` writes its arguments into provenance rows. The first fixture is what caught the 2^53 bug, which the record oracle could not, because the store holds no value that large.

`tests/fixtures/record-schema.json`, `tests/fixtures/vocabularies.json`, `tests/fixtures/turtle.json` and `scripts/expected/store-schema.json` are the same idea one level up: what the data repository's own TypeScript does, with zod as the schema and n3 as the Turtle parser. `scripts/fixtures/record_schema_fixture.mjs` writes them, taking its Turtle documents from `scripts/fixtures/turtle_cases.mjs`. `tests/fixtures/validation.json`, `scripts/expected/store-validation.json` and the attribute registry in `src/rewire_evidence/data/` are what its validators do, written by `scripts/fixtures/record_validation_fixture.mjs` from the cases in `scripts/fixtures/validation_cases.mjs`. `tests/fixtures/write-path.json` is what its write path does: `scripts/fixtures/record_write_fixture.mjs` gives `addBatch`, `recordChanges` and `applyChanges` the stores and inputs `scripts/fixtures/write_cases.mjs` builds, with the clock held still, and writes down what each returned or refused and every file it left different. The same recorder writes `scripts/expected/store-recorded-writes.json` and the changes files in `scripts/expected/changes/`. All three recorders have to run inside a checkout of the data repository with its dependencies installed. That happens in the `fixtures` job in CI and nowhere else. The same job records `src/rewire_evidence/data/lowercase.json` with `scripts/fixtures/record_lowercase.mjs`: what `toLowerCase` does to every code point on the Node the data repository runs on, which the library reads so that lowercase does not follow the Unicode version of the Python it runs on. The locator grammar is 28 patterns, and what a JavaScript pattern matches is not what Python's `re` matches for the same text: `\p{L}` follows the Unicode version, a letter under the `i` flag follows JavaScript's own rules for case, and without the `u` flag a pattern counts UTF-16 units. So the sets are data too. `scripts/fixtures/pattern_requests.json` lists every class, escape and letter the library's patterns hold, `scripts/fixtures/record_pattern_fixture.mjs` records the characters Node matches for each into `src/rewire_evidence/data/pattern-classes.json`, and `jsregex.py` writes each pattern out for `re` from those ranges with no flags. `python scripts/fixtures/list_pattern_requests.py` writes the list, and a test fails while it is stale. The same recorder writes `tests/fixtures/js-patterns.json`: what Node matches for the patterns and texts in `scripts/fixtures/pattern_cases.json`, and what `toFixed` writes. `tests/fixtures/locator.json` is the grammar itself, recorded by `scripts/fixtures/record_locator_fixture.mjs` from the cases in `scripts/fixtures/locator_cases.mjs`: the patterns as the TypeScript holds them, its reading of 2,321 locators, and 16 runs of its command. `tests/fixtures/checks.json` is the checks pack, recorded by `scripts/fixtures/record_checks_fixture.mjs` from the cases in `scripts/fixtures/checks_cases.mjs`: what `parseCell` makes of 398 cells, the findings of 19 small stores, 18 baselines held against runs, and 14 runs of its command. The same recorder writes `src/rewire_evidence/data/powers-of-ten.json`, because `10 ** n` on the Node the data repository runs is not the nearest double for every n. `tests/fixtures/tables.json` is the table reader, recorded by `scripts/fixtures/record_tables_fixture.mjs` from the cases in `scripts/fixtures/tables_cases.mjs`: every pattern written in `tables.ts` as the TypeScript compiler reads the file, and what the reader makes of references, fragments, 59 sources, captions, sections, headers and text layers.

The job fails if a committed file is not what the TypeScript produces, and uploads what it recorded as the `schema-fixtures` artifact, which is how a changed fixture gets committed. `scripts/fixtures/compare_patterns.py` and `compare_patterns.mjs` put patterns together at random and compare what `jsregex` and a local Node make of them, for looking at a new construct before its cases are written; they are not a gate and CI does not run them. The workflow names Node by its exact version and each action by a commit, the recordings name the Node they were made with, and a test requires the two to agree, so what does the recording cannot move without a commit.

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
src/rewire_evidence/writes.py       records add and records change: what a write leaves, written and undone
src/rewire_evidence/jsregex.py      JavaScript patterns run by re, from sets of characters recorded from Node
src/rewire_evidence/locator.py      the locator grammar: parse, render, classify, and the version 1 pattern
src/rewire_evidence/locators.py     the locators of a store: the coverage report, and what the locator gate measures
src/rewire_evidence/cells.py        a printed table cell as its number and its spread: parseCell
src/rewire_evidence/tables.py       tables read out of an HTML or JATS artifact, and rows out of a text layer
src/rewire_evidence/checks.py       the seven report-only checks, and the store and vocabularies they read
src/rewire_evidence/ratchet.py      the baseline of the checks, the ratchet on it, and the two commands
src/rewire_evidence/cli.py          the rewire-evidence command: records check, descriptions, add, change; locator; checks
src/rewire_evidence/parity.py       what the digest and layout gates measure
src/rewire_evidence/conformance.py  what the schema gate measures
src/rewire_evidence/agreement.py    what the validation gate measures
src/rewire_evidence/replay.py       what the write gate measures: merged batches replayed from history
src/rewire_evidence/checkparity.py  what the checks gate measures: the committed first run and the recorded one
src/rewire_evidence/tableparity.py  what the table gate measures: every committed artifact as the TypeScript read it
src/rewire_evidence/gates.py        the gates as commands: arguments, report, exit status
scripts/                            one thin file per gate, the expectations, the fixture recorders
tests/                              pytest and Hypothesis, with the Node fixtures
docs/stages/                       one note per ported stage: oracle, measured result, known gaps
```

## Licence

The code is under the MIT licence: see [LICENSE](LICENSE). `src/rewire_evidence/turtle.py` is a port of part of n3, which is also MIT; its notice is in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). The records this library reads and writes live in `rewire-benchmark-data` and are not covered by this licence.
