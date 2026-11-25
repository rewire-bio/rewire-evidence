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
| P9 | The archive read side and the licence map: the manifest and receipt schemas, the manifest writes a takedown depends on, the object cache check, the work list, the classifier, candidates, the triage queue, `reclassify`, and the `archive` and `licence` commands that ask no network | every manifest row, receipt and candidate of the pinned checkout read by its schema and written back as its committed line; one recorded line for each, with the tier the TypeScript's classifier gives it; the work list, the triage queue and the reclassification plan as the TypeScript holds them; nine commands as it prints them; the 22 files read, each by its digest; the committed triage queue written again byte for byte from the commit that wrote it; 1,382 rows recorded from zod, 1,090 of them refused; 292 calls and 43 commands recorded on 18 small trees | 1,167 manifest rows, 1,526 receipts, 1,091 candidates, 1,497 sources, 9,696 recorded lines, 22 files, 1,083 lines of the committed queue | Passing ([notes](docs/stages/p9.md)). The network side is P14 |
| P10 | Layer derivation: classification from the bytes, the gzip, tar and zip readers, `text-utf8@1`, the markup scanner with `europepmc-fulltext-xml@1` and `html-text@1`, `ooxml-sheet-tsv@1` and `ooxml-document-text@1`, the two table grids and the candidate header rule, `pdf-text-layer@1` through the pinned pdftotext, the sanity gate, the registry build, and the `layers build`, `check` and `thresholds` commands | every line of `data/omics/layers/registry.jsonl` and `unsupported.jsonl` derived again from the stored bytes and required whole, from the manifest of the commit that wrote them; the two files written again from their own rows; what the pinned manifest would write otherwise, measured and pinned; 908 cases and 32 command runs recorded from the TypeScript | 871 public objects, 2,240 of 2,595 registry lines and 232 of 245 unsupported lines in CI; all 1,167 objects and every line once on a machine that holds them | Passing for the public objects ([notes](docs/stages/p10.md)). 355 lines of 296 restricted objects are not checked in CI. The readers of a layer are P11 |
| P11 | Span resolution: the offset index, the resolvers for a table cell in aligned text, a cell of a table grid, a delimited cell, a sheet cell, a JSON value, a quote, a paragraph, a caption, a line and a page, the dispatch, re-anchoring, the measurement with its five tables and its figures file, the layer readers and the coverage table, and the `spans` commands with `layers coverage`, `surface` and `show` | one line for each result as the TypeScript's own measurement decides it, under both arms of the quote rule, with the layer and both ranges of its span or the reason it was refused; every file the measurement writes, byte for byte; the five committed tables of 2026-11-19, the 173 committed figures and the committed figures file byte for byte but for one input that has gained records; 1,366 calls, 35 steps on the figures file and 74 command runs recorded from the TypeScript, and 228,000 calls on generated input compared with it on every run | 52,278 results: 41,153 measured in CI over the 871 public objects and held to the TypeScript's lines, 11,125 held to a recording over all 1,167; all 52,278 measured once on a machine that holds every object | Passing ([notes](docs/stages/p11.md)). The results that need a restricted object are not measured in CI |
| P12 | Extractor equivalence | position-joined candidate comparison | 418,440 | Not started |
| P13 | Knowledge graph export, attribute terms, draft block, SHACL validation, release bundle publisher, and Node-free write-path shape checking | every asserted N-Quads line of `public/kg/asserted.nq` by overall SHA-256 and 50k-line chunk digests; `context.jsonld`, `rb-attributes.ttl`, `attribute-shapes.ttl` and manifest `draft` block byte for byte; 24 P5 validator subset cases and 12 P6 write-path subset/SHACL cases; in-process Node-free SHACL check on `records add` and `records change` | 93,216 records (92,746 public), 2,445,824 asserted quads across 49 chunks, 4 ontology modules, 36 subset/SHACL cases | Passing ([notes](docs/stages/p13.md)) |

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
uv run python scripts/verify_store_archive.py ../rewire-benchmark-data
uv run python scripts/fetch_public_objects.py ../rewire-benchmark-data ../objects
uv run python scripts/verify_store_layers.py ../rewire-benchmark-data ../rewire-benchmark-data-layers-ref ../objects
uv run python scripts/verify_store_spans.py ../rewire-benchmark-data ../objects
uv run python scripts/verify_store_kg.py ../rewire-benchmark-data
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

The ninth reads the archive manifest, the fetch and licence receipts, the licence map, the candidates and the source records as the archive read side reads them, and writes a line for each: a manifest row with its schema's verdict, the digest of the line written back from what the schema read, the tier the classifier gives the object and how `reclassify` reads it; a source with its work item, its tier and its row in the triage queue. Every line has to be the one the TypeScript wrote at the same commit, which is `scripts/expected/store-archive.jsonl.gz`, 9,696 lines recorded in CI by `scripts/fixtures/record_archive_fixture.mjs`. Every row a schema reads has to be written back as the line the checkout commits, the work list summary and the reclassification plan have to be the TypeScript's, nine commands have to print what its commands printed, and each of the 12 TypeScript files the port was made from has to be the file recorded. So does each of the 22 files of the checkout the read side reads, by its SHA-256, so a file framed otherwise fails though every line of it reads the same, and the lines of the committed triage queue that the queue generated still holds have to be as many as the TypeScript counted. A step after the gate checks out the commit that wrote the committed queue (`QUEUE_REF`) and requires `licence triage` to write that file again byte for byte. The recording was made of a checkout with no object cache, and the gate does not run on one that has a cache. For a working checkout that has one, set `ARCHIVE_CACHE` to an empty folder for the run.

The tenth derives every text layer and table grid again from the stored bytes and requires each line of `data/omics/layers/registry.jsonl` and `unsupported.jsonl`, whole. The bytes are not in a checkout, so it reads a folder of objects laid out as the cache is, which `scripts/fetch_public_objects.py` fills from the release assets of the 871 objects a public release mirrors. That script is the only one here that asks a network for anything, and its work is in `rewire_evidence.publicobjects`. A public object missing from the folder fails the gate, and a restricted one is counted as not checked. A registry row copies three members from the manifest, which has moved since the registry was written, so the gate takes them from a second checkout at the commit that wrote the registry, `e70b112`, and reports what the pinned manifest would write instead. It runs pdftotext and pdfinfo, and refuses any but version 26.08.0 of each, which CI builds from source on macOS with `scripts/build_poppler.sh` because no package index serves it and the Linux build writes other bytes. The expectation states the pinned manifest by digest and the sizes a run has to reach, so the script fails by itself on a thinner checkout. The [P10 notes](docs/stages/p10.md) have the measurements.

The eleventh resolves the locator of every result against the layers the same folder of objects gives, as `npm run spans -- measure` does, and writes what that command writes with one line more for each result. The data repository commits totals, which cannot show that two resolvers agree on a span, so the oracle is recorded: `scripts/fixtures/record_spans_store.mjs` runs the TypeScript's own measurement, in the `layers` job over the public objects on every run, and once over every object, whose lines are `scripts/expected/store-spans.jsonl.gz` with each text as a digest. Every file has to be the TypeScript's for the objects held. Each result those objects decide has to have the line the full recording has, and the rest are taken from it and counted. The five committed tables and the committed figures then have to be what the results give, and the committed figures file has to be the bytes they give, with the one input that has gained records since left as it is. Both files of recorded lines are held to their digests, and the lines to the results in their order. The recorder runs the pinned `measure-store-spans.ts` itself and requires its bytes. It needs the pinned pdftotext, as the tenth does.

The twelfth exports the asserted N-Quads graph (`public/kg/asserted.nq`), `context.jsonld`, `rb-attributes.ttl`, `attribute-shapes.ttl`, and the manifest `draft` block, checks every 50,000-line chunk SHA-256 and artifact digest against `scripts/expected/store-kg.json`, and verifies 36 recorded P5/P6 subset exports and `pyshacl` `--shapes-only` verdicts. The [P13 notes](docs/stages/p13.md) have the measurements.

All twelve exit 0 when everything reproduces, 1 on a disagreement and 2 when the run could not be made: a store, a vocabulary, a history or an expectation that cannot be read, or a report that cannot be written. No one gate holds every byte. What each catches of an edited checkout is set out in the [P2 and P3 notes](docs/stages/p2-p3.md), and CI runs all eleven (`fetch_public_objects.py` is the download helper).

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

The archive has four, which are `npm run archive -- worklist`, `verify`, `status` and `reclassify`, and the licence map two, which are `scripts/licence/triage.ts` and `counts.ts`. None asks a network for anything. `verify` holds every object in the cache to the manifest and exits 1 if one is missing, corrupt or unlisted. `reclassify` reads every manifest row's licence again and writes the tier it justifies, and writes nothing at all if any row would move from public to restricted, which is a takedown:

```sh
uv run rewire-evidence archive worklist --root ../rewire-benchmark-data
uv run rewire-evidence archive status --root ../rewire-benchmark-data --json
uv run rewire-evidence archive verify --root ../rewire-benchmark-data
uv run rewire-evidence archive reclassify --root ../rewire-benchmark-data --dry-run
uv run rewire-evidence licence triage --root ../rewire-benchmark-data --stdout
uv run rewire-evidence licence counts ../rewire-benchmark-data
```

The layer registry has six, which are `npm run layers -- build`, `check`, `thresholds`, `coverage`, `surface` and `show`. `build` writes the two generated files under `data/omics/layers/` from the manifest and the object cache of the checkout, and `check` says whether the committed ones are what the store produces. `coverage` puts every source in one bucket by what the archive holds for it, `surface` says how much text that is, and `show` lists the layers of a source or an object and prints the opening of the best one. `build`, `check` and `show` read the object cache, and a PDF layer needs the pinned pdftotext:

```sh
uv run rewire-evidence layers check --root ../rewire-benchmark-data
uv run rewire-evidence layers thresholds
uv run rewire-evidence layers coverage --root ../rewire-benchmark-data
uv run rewire-evidence layers show --root ../rewire-benchmark-data a-source-id
```

Span resolution has five, which are `npm run spans -- measure`, `retag`, `control`, `value-check` and `show`. `measure` resolves the locator of every result, prints five tables and writes them under `maintenance/reports/` in the checkout, or where `--out` says, with the figures file where they go to the checkout. It writes nothing when an input moved while it measured. `--date` names the tables, and `SPAN_DETAIL` names a file for one line to each result, as it does for the TypeScript. All five read the object cache:

```sh
uv run rewire-evidence spans measure --root ../rewire-benchmark-data --out spans
uv run rewire-evidence spans show --root ../rewire-benchmark-data a-result-id
```

So are the commands that write. Each prints the line the TypeScript prints, and with `--dry-run` writes nothing and prints the lines each store file would lose and gain:

```sh
uv run rewire-evidence records add --root ../rewire-benchmark-data --dry-run batch.jsonl data/omics/a-batch
uv run rewire-evidence records change --root ../rewire-benchmark-data docs/reviews/a-review.md data/omics/a-batch an-id another-id
uv run rewire-evidence records change --root ../rewire-benchmark-data docs/reviews/a-review.md data/omics/a-batch --changes changes.jsonl
```

Without `--dry-run` they write every store file as the TypeScript writes it and then hold what they wrote to the SHACL shapes in process via `subset_quad_lines` and `shapes_only_check`, with no Node or `npm` installation required. If the batch, the change or the shapes are refused, every store file is put back as it was, a store file that was not there is removed, and the command exits 1.

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

`tests/fixtures/record-schema.json`, `tests/fixtures/vocabularies.json`, `tests/fixtures/turtle.json` and `scripts/expected/store-schema.json` are the same idea one level up: what the data repository's own TypeScript does, with zod as the schema and n3 as the Turtle parser. `scripts/fixtures/record_schema_fixture.mjs` writes them, taking its Turtle documents from `scripts/fixtures/turtle_cases.mjs`. `tests/fixtures/validation.json`, `scripts/expected/store-validation.json` and the attribute registry in `src/rewire_evidence/data/` are what its validators do, written by `scripts/fixtures/record_validation_fixture.mjs` from the cases in `scripts/fixtures/validation_cases.mjs`. `tests/fixtures/write-path.json` is what its write path does: `scripts/fixtures/record_write_fixture.mjs` gives `addBatch`, `recordChanges` and `applyChanges` the stores and inputs `scripts/fixtures/write_cases.mjs` builds, with the clock held still, and writes down what each returned or refused and every file it left different. The same recorder writes `scripts/expected/store-recorded-writes.json` and the changes files in `scripts/expected/changes/`. All three recorders have to run inside a checkout of the data repository with its dependencies installed. That happens in the `fixtures` job in CI and nowhere else. The same job records `src/rewire_evidence/data/lowercase.json` with `scripts/fixtures/record_lowercase.mjs`: what `toLowerCase` does to every code point on the Node the data repository runs on, which the library reads so that lowercase does not follow the Unicode version of the Python it runs on. The locator grammar is 28 patterns, and what a JavaScript pattern matches is not what Python's `re` matches for the same text: `\p{L}` follows the Unicode version, a letter under the `i` flag follows JavaScript's own rules for case, and without the `u` flag a pattern counts UTF-16 units. So the sets are data too. `scripts/fixtures/pattern_requests.json` lists every class, escape and letter the library's patterns hold, `scripts/fixtures/record_pattern_fixture.mjs` records the characters Node matches for each into `src/rewire_evidence/data/pattern-classes.json`, and `jsregex.py` writes each pattern out for `re` from those ranges with no flags. `python scripts/fixtures/list_pattern_requests.py` writes the list, and a test fails while it is stale. The same recorder writes `tests/fixtures/js-patterns.json`: what Node matches for the patterns and texts in `scripts/fixtures/pattern_cases.json`, and what `toFixed` writes. `tests/fixtures/locator.json` is the grammar itself, recorded by `scripts/fixtures/record_locator_fixture.mjs` from the cases in `scripts/fixtures/locator_cases.mjs`: the patterns as the TypeScript holds them, its reading of 2,321 locators, and 16 runs of its command. `tests/fixtures/checks.json` is the checks pack, recorded by `scripts/fixtures/record_checks_fixture.mjs` from the cases in `scripts/fixtures/checks_cases.mjs`: what `parseCell` makes of 398 cells, the findings of 19 small stores, 18 baselines held against runs, and 14 runs of its command. The same recorder writes `src/rewire_evidence/data/powers-of-ten.json`, because `10 ** n` on the Node the data repository runs is not the nearest double for every n. `tests/fixtures/tables.json` is the table reader, recorded by `scripts/fixtures/record_tables_fixture.mjs` from the cases in `scripts/fixtures/tables_cases.mjs`: every pattern written in `tables.ts` as the TypeScript compiler reads the file, and what the reader makes of references, fragments, 59 sources, captions, sections, headers and text layers. `tests/fixtures/archive.json` is the archive read side and the licence map, recorded by `scripts/fixtures/record_archive_fixture.mjs` from the cases in `scripts/fixtures/archive_cases.mjs`: what zod says of 1,382 manifest rows, receipts and candidates, how Node expands 44 gzip files and reads 126 URLs, and 18 small trees with every call made on them, what each returned or threw, every file it left different, and what the commands printed. `tests/fixtures/layers.json` is layer derivation, recorded by `scripts/fixtures/record_layers_fixture.mjs` from the cases in `scripts/fixtures/layers_cases.mjs`: what `classify`, the container readers, the markup scanner, the Office readers, the grid builder, the sanity gate and the registry build make of 885 inputs the stored objects have no like of, and 24 runs of the `layers` command on three small trees.

`tests/fixtures/spans.json` is what its span resolvers do: the offset index, each resolver, the dispatch, the value check, the layer readers and the figures file on layers and locators the stored results have no like of, and its span and layer commands on small trees, written by `scripts/fixtures/record_spans_fixture.mjs` from `spans_cases.mjs`. It also holds what `scripts/fixtures/record_spans_store.mjs`, the recorder of the span gate, writes for one of those trees with every object and with two taken out, and the tests run the gate against both. `src/rewire_evidence/data/case-units.json` is what each UTF-16 unit matches under the `i` flag, written by `scripts/fixtures/record_case_units.mjs` with built-ins only, for the patterns the resolvers build from a locator.

The job fails if a committed file is not what the TypeScript produces, and uploads what it recorded as the `schema-fixtures` artifact, which is how a changed fixture gets committed. `scripts/fixtures/compare_patterns.py` and `compare_patterns.mjs` put patterns together at random and compare what `jsregex` and a local Node make of them, for looking at a new construct before its cases are written; they are not a gate and CI does not run them. `scripts/fixtures/compare_spans.mjs` and `compare_spans.py` do the same for the span resolvers, with locators and text put together at random. They need a checkout of the data repository with its dependencies installed, so the job runs them: 228,000 calls from a fixed seed on every push, failing on any difference, with the calls kept as the `span-calls` artifact. The workflow names Node by its exact version and each action by a commit, the recordings name the Node they were made with, and a test requires the two to agree, so what does the recording cannot move without a commit.

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
src/rewire_evidence/weburl.py       whether JavaScript's URL parser reads a piece of text, and the host it reads
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
src/rewire_evidence/archive.py      the archive contract: manifest rows and receipts, the manifest writes, the object cache
src/rewire_evidence/worklist.py     the fetch work list from the source records, and what the archive holds of it
src/rewire_evidence/licence.py      the licence map, the tier it gives an object, candidates, the triage queue
src/rewire_evidence/reclassify.py   every manifest row's licence read again, and the tier it justifies written
src/rewire_evidence/archivesaid.py  what the archive and licence commands print
src/rewire_evidence/classify.py     what class of bytes an object holds, which decides the normaliser
src/rewire_evidence/containers.py   gzip, tar and zip expanded to their members
src/rewire_evidence/textlayer.py    the shape every normaliser returns
src/rewire_evidence/markup.py       the tag scanner the XML and HTML normalisers and the table grid share
src/rewire_evidence/office.py       text layers for Office packages: a workbook as sheet blocks, a document as lines
src/rewire_evidence/grids.py        the table grid as a second layer, and the candidate header rule
src/rewire_evidence/pdftext.py      a PDF through the pinned pdftotext, and its page count
src/rewire_evidence/layers.py       the layer tree of one stored object
src/rewire_evidence/sanity.py       whether a layer can be used: the counts and the verdict
src/rewire_evidence/registry.py     the layer registry, built and read
src/rewire_evidence/layersaid.py    what layers build, check and thresholds print
src/rewire_evidence/publicobjects.py the objects a public release mirrors, fetched into a folder
src/rewire_evidence/offsets.py      a layer as JavaScript holds it, and a span of it in characters and in bytes
src/rewire_evidence/resolution.py   what a resolver returns: a span, or one reason out of a closed list
src/rewire_evidence/aligned.py      a table cell in column-aligned text, the width check, a figure caption
src/rewire_evidence/regions.py      a delimited cell, a keyed row, a line range, a page
src/rewire_evidence/prose.py        a quote, a paragraph of a section, and a stored span found again
src/rewire_evidence/jsonspans.py    a JSON document read with its positions, and the value a pointer names
src/rewire_evidence/sheets.py       the blocks of a sheet layer, and a cell by sheet, row and column
src/rewire_evidence/gridspans.py    a grid layer read back, and a cell crossed against it
src/rewire_evidence/resolve.py      which resolver a locator's shape calls for
src/rewire_evidence/layerreaders.py the text of a registered layer, derived again and held to its hash
src/rewire_evidence/layercoverage.py every source in one bucket by what the archive holds for it
src/rewire_evidence/figures.py      the figures a document prints, tied to the inputs they were measured over
src/rewire_evidence/spanmeasure.py  every stored locator resolved, the value check, the five tables
src/rewire_evidence/spansaid.py     what the span commands and the layer reading commands print
src/rewire_evidence/kgterms.py      OWL declarations and SHACL attribute shapes generated from the registry
src/rewire_evidence/kgdraft.py      manifest draft JSON Schema block for typed editing
src/rewire_evidence/kgexport.py     JSON-LD context, N-Quads export, and subset extraction
src/rewire_evidence/kgshapes.py     OWL 2 RL inference and SHACL shape validation
src/rewire_evidence/kgpublish.py    immutable GitHub release bundle packing and verification
src/rewire_evidence/cli.py          the rewire-evidence command: records, locator, checks, archive, licence, layers, spans, kg
src/rewire_evidence/parity.py       what the digest and layout gates measure
src/rewire_evidence/conformance.py  what the schema gate measures
src/rewire_evidence/agreement.py    what the validation gate measures
src/rewire_evidence/replay.py       what the write gate measures: merged batches replayed from history
src/rewire_evidence/checkparity.py  what the checks gate measures: the committed first run and the recorded one
src/rewire_evidence/tableparity.py  what the table gate measures: every committed artifact as the TypeScript read it
src/rewire_evidence/archiveparity.py what the archive gate measures: the manifest, receipts and licence map as the TypeScript read them
src/rewire_evidence/layerparity.py  what the layer gate measures: every committed registry line derived again
src/rewire_evidence/spanparity.py   what the span gate measures: every result as the TypeScript's measurement decides it
src/rewire_evidence/kgparity.py     what the KG gate measures: asserted N-Quads, ontology artifacts, and subset SHACL checks
src/rewire_evidence/gates.py        the gates as commands: arguments, report, exit status
scripts/                            one thin file per gate, the expectations, the fixture recorders
tests/                              pytest and Hypothesis, with the Node fixtures
docs/stages/                       one note per ported stage: oracle, measured result, known gaps
```

## Licence

The code is under the MIT licence: see [LICENSE](LICENSE). `src/rewire_evidence/turtle.py` is a port of part of n3, which is also MIT; its notice is in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). The records this library reads and writes live in `rewire-benchmark-data` and are not covered by this licence.
