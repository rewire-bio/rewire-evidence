# Handover, 2026-11-20

Written on request, at the end of the session that created this repository. It covers what is built, what is not, and the things that are true but not written down anywhere else.

Two notes on its scope, so nobody reads more into it than it says. The request came relayed through another agent session rather than directly, so this document hands over **knowledge**, which is all one session can give another. Who owns which files afterwards is the owner's call, not something this document settles. And where a figure appears below it was measured today, not recalled; where something is uncertain it says so.

---

## 1. rewire-evidence, the Python rebuild

### What is built and passing

One module. That is not an understatement.

| | |
| --- | --- |
| `src/rewire_evidence/canonical.py` | 166 lines, 67 statements |
| `tests/test_canonical.py` | 74 tests, all passing |
| `scripts/verify_store_digests.py` | the acceptance gate, 124 lines |
| Statement coverage of the module | 99%, one line uncovered (`canonical.py:116`, the `TypeError` for a type with no `JSON.stringify` form) |
| Gate result | **93,216 of 93,216** committed record digests reproduce |

CI (`.github/workflows/checks.yml`) runs ruff, the tests, and the gate against a **pinned** commit of `rewire-benchmark-data` (`STORE_REF`), asserting the expected record count (`STORE_RECORDS`) so a thinner checkout cannot pass while proving less. Bumping the pin is a deliberate commit.

### What is stubbed

Everything else. There is no archive code, no layer derivation, no locator grammar, no span resolution, no extraction, no label stage. The 99% coverage figure covers about 4% of the eventual library, and reading it as a project-level number would be a mistake. If the goal is the full ingestion tooling in Python at 100% coverage, treat today's state as stage 0 of six complete.

### The stage plan, and the gate for each

The discipline is the point: nothing is accepted because its unit tests pass, only when it reproduces data that already exists. The old store is big enough to be decisive.

| Stage | Source in TypeScript | Lines | Gate |
| --- | --- | --- | --- |
| 0. Serialisation and digests | `scripts/omics/records.ts`, `shared/archive/schema.ts` | 626 | 93,216 record digests. **Passing** |
| 1. Objects, manifest, receipts | `scripts/archive/*` | 2,690 | 1,167 manifest rows round-trip byte-identically |
| 2. Layer derivation | `shared/archive/layers/*` | 3,800 | all 2,595 registry rows reproduce their `layer_sha256`, reported per normaliser |
| 3. Locator grammar | `shared/omics/locator.ts` | 568 | 52,278 locators parse identically |
| 4. Span resolution | `shared/omics/spans/*` | 3,262 | the 18,667 that resolve today resolve identically, with no regression in refusal reasons |
| 5. Extraction | `shared/omics/extract/*` | 2,550 | the position-joined comparison still reports 0 disagreements and 0 inventions over 418,440 candidates |
| 6. Consolidate the batch scripts | `data/omics/**/*.py` | 114,117 across 412 files | per-wave match rate against the store |

Stage 2 is the hard one and should be attempted in the order `text-utf8@1`, `ooxml-sheet-tsv@1`, then the markup and PDF paths, because the first two are cheap and tell you early whether byte-exact transcription is achievable at all. Do **not** bump a normaliser to `@2` to make a mismatch go away: that destroys the 2,595-row hash oracle, which is the only thing making the port verifiable.

Stage 6 is 114,117 lines of Python but it is not a port. Those 412 scripts carry 42 copies of one helper in 11 drifted versions; the task is to extract the small shared core (`slug`, `num`, `sha`, a review writer, a finaliser) and re-run the batches against the store.

### How to run it

```sh
# tests
uv run --python 3.12 --with pytest --with-editable . python -m pytest

# tests with coverage
uv run --python 3.12 --with pytest --with pytest-cov --with-editable . python -m pytest --cov=rewire_evidence --cov-report=term-missing

# the parity gate, against any checkout of the data repo
uv run --python 3.12 --with-editable . python scripts/verify_store_digests.py ../rewire-benchmark-data

# lint
uv run --python 3.12 --with ruff ruff check . && uv run --python 3.12 --with ruff ruff format --check .
```

The gate takes a path, so it can be pointed at a worktree. It writes nothing anywhere, exits 1 on a digest mismatch and 2 on a setup error, and prints the first few mismatches with the committed and computed digest so a failure names a cause.

### Parity gaps, stated precisely

These are the places where "reimplement it in Python" is not safe, and they are the reason this repository exists at all.

1. **A record's identity is `sha256(JSON.stringify(record))`.** Not of a canonical form. Of JavaScript's serialisation, in insertion order. So the Python has to agree with JavaScript on how a *number is written*, not merely on its value.
2. **Three different serialisations are in use and they are not interchangeable.** `JSON.stringify` (insertion order, hashed into record digests); `canonicalLine` (top-level keys sorted only, `shared/archive/schema.ts`, used for manifest and receipt lines); `canonicalJson` (sorted at every depth, `shared/archive/layers/grid/index.ts`, and those bytes are inside a `layer_sha256`). `json.dumps(sort_keys=True)` is the third one. Using it where the second belongs writes valid, sorted, wrong bytes on any row holding a nested object, and the manifest holds many.
3. **The store holds no spans.** `shared/omics/locator.ts:31` says so: they are derived at measure time. This removes one argument for transcribing the scanners character for character, but the conclusion stands on the hash oracle instead.
4. **gzip is not byte-stable across zlib versions.** `scripts/archive/pull.ts` keeps the compressed bytes it received rather than recompressing, deliberately. Any Python rewrite must do the same.

### The next three things I would have done

1. **Stage 1, the manifest.** It is the first real use of `canonical_line` and therefore the first test of the top-level-sort-only distinction the kernel was built to get right. Gate: re-emit all 1,167 rows and require byte equality. Four days, with a reportable number on day two.
2. **Raise the one uncovered line to 100%** and add a `ruff` rule set beyond `E,F,I,UP,B` if the 100% coverage goal is real, because the thing that will actually bite is a branch in `js_number`, not a missing test file.
3. **Settle the label-stage acceptance check** before building any Python around it. See section 7: it is the single largest measured loss in the pipeline and it is a decision, not a task.

---

## 2. The rebuild (#274) and the repository move (#275)

### Repository move: executed as far as it safely can be

`rewire-bio/rewire-benchmark-data-next` holds the history with the release bundles removed.

| | |
| --- | --- |
| Pack as GitHub serves it | 4.44 GiB |
| After the filter and the ref cleanup | **777.80 MiB** |
| Branches / tags / commits on main | 11 / 178 / 695, all preserved |
| Release receipts | 118, all preserved |
| `.gz` bundles in history | 0 |
| Remote `main` tip | `2373256`, verified equal to the filtered history |

Created with issues, wiki and projects disabled and a description saying it is not authoritative, so nothing can land in it by accident.

**The recipe is four lines, not the two in the issue.**

```sh
git clone --mirror https://github.com/rewire-bio/rewire-benchmark-data.git
git filter-repo --invert-paths --path-glob 'data/omics/releases/*.gz'
git for-each-ref --format='delete %(refname)' refs/pull | git update-ref --stdin
git reflog expire --expire=now --all && git gc --prune=now
```

Two corrections, both of which cost something if missed:

- **The issue says to drop `data/omics/releases/`.** That path also holds the 118 committed release receipts, which `AGENTS.md` names as how releases are recorded and which are the only record of what each release contained. My first dry run deleted all of them. The glob is what keeps them.
- **A `--mirror` clone brings `refs/pull/*`, and it brought 212.** This nearly defeated the exercise: the whole argument for a new repository is that those refs cannot be stripped from the old one and keep the dead objects reachable, so importing them carries the same pins across. They are also why a fresh GitHub mirror measured 981.86 MiB where a local dry run measured 723 MiB. A `push --mirror` carrying them is rejected anyway, since that namespace is read-only on GitHub.

### Is `-next` still needed?

**Not as it stands, no.** It is a demonstration, not a destination, and it goes stale with every merge into the old repository. Its value is the verified recipe and the measured 777.80 MiB. At the real cutover it must be re-filtered from a fresh clone taken at a quiet point. Keeping it costs nothing and deleting it loses nothing except the demonstration; what must not happen is anyone starting to use it.

### What is left, in order

1. Lane 5 steps 1 to 4 (#274): the rebuilt store with a parity report, so nothing depends on re-fetching what the old store cites.
2. Fresh clone, four-line recipe above. Verify the receipt count against `origin/main` at that moment, not a local branch: a stale local `main` reports 110 where `origin/main` has 118, which is what the first check of this looked like.
3. Push before any pull request exists in the target, so no `refs/pull/*` can ever pin the old objects there.
4. **Rebuild the release assets.** This is the step that makes it a coordinated window rather than a push. Assets live on tags, not in git objects, so a filtered clone arrives with none: 57 release tags plus 16 `archive/objects-<shard>` tags carrying 871 object mirrors. Until they exist, the old repository is the only place those bytes are served from.
5. Repoint the website lock and serving contract in `rewire-bio/rewire-database` in the same window, so release-asset URLs change once.
6. Settings: 9 secrets, 4 variables, ruleset 27641834 with its 14 contexts and both bypass actors, 6 project labels. The full inventory is in #275. **Secret values cannot be read out of GitHub by any tool**, so the owner re-enters them.
7. The 12 hardcoded repository-name sites, listed in #275. Two of them break hardest and were on nobody's list: the app-token scopes (now fixed in #330 and #331 so they scope to the running repository), and two User-Agent strings sent to publishers (`scripts/archive/fetch.ts:48`, `scripts/licence/resolve.ts:344`). Leave `scripts/kg/export.ts:324` alone: it is a dataset identifier in published RDF.
8. Transfer issues, archive the old repository read-only, rename.

Agent schedules should be in report-only mode for the window; the factory session offered to set the `*_MODE` variables.

---

## 3. Checks ratchet, rules, vocabulary, record contract, ADRs

I worked on none of the rules streams today, so this section is what the issues say plus what I verified, and it is explicitly not a status report on work I did.

**Settled, by owner determination on 2026-11-17** (seven rulings, recorded in #276, Lane 7):

1. NonCommercial use is permitted, on the stated premise that the data and graph are never sold, licensed to a partner or paywalled. About 97 sources carrying ~16,800 citations move from private to publishable by a manifest flag, no refetch. **The determination must record the premise**, because if the premise changes the tier is re-evaluated rather than inherited.
2. A derived text layer of a no-derivatives work may be published, following the original's tier. A `pdftotext -layout` layer is a format conversion, not adapted material.
3. The seven restrictively licensed originals already committed stay in history with `exposure: pre-policy`.
4. Takedown contact is tim@rewire.it, and the path must exist before the first public upload.
5. Restricted bytes live in Cloudflare R2. **Now satisfied in practice**: the four secrets exist and CI has used them.
6. **No human review anywhere.** A span-verified machine claim may enter the published graph. Receipts must say a claim is machine-extracted, span-verified and unreviewed, and the reviewer field carries a tool identity, not a person. `docs/collection.md` as currently written still describes a human review step and has to change with it.
7. Scope is the documented use-case set (`docs/reviews/use-cases/` and rewire.it #365), not a modality list.

**Open, and waiting on the owner:**

- **The ratchet shape (#272).** ADR 0002's question 2 as posed is answered by measurement and both its options are wrong. The recommendation on the issue: hard-fail the seven rules that never rose on an evidence pull request, leave the five convention-dominated rules advisory with a tracked count. Not decided.
- **The unit vocabulary convention (Lane 3 step 6).** Measured smaller than the ADR estimated: 230 of 275 concepts have one observed unit and are a generated line; the contested set is 36 concepts, of which 25 are a single convention question. The proportion-metric convention is recorded as a tooling ruling, not an owner decision.

**ADRs:** 0001 comparable evidence, 0002 claim extraction, 0003 source preservation, 0004 storage substrate, 0005 run recipes, all under `docs/adr/`. Four move from Proposed to Accepted once the Lane 7 determinations are written down. That writing is documentation work, not engineering.

**Discrepancy rules v4 and v5: not mine and not touched.** There is a `wt/discrepancy-v4` worktree and the main checkout sits on a `claude/discrepancy-rules-v5` branch, neither created by this session. Known from earlier context: the v4 stack was PR #216 on #215 on #212 and **merge order matters for the orphan check**. Confirm current state from the issues rather than from this paragraph.

---

## 4. Branches, pull requests, worktrees and background jobs

### Merged today, all mine, all clean

| PR | What | State |
| --- | --- | --- |
| #319 | Check the drift inputs record by record, not by position | Merged `1173c8a`. Remote branch auto-deleted |
| #321 | Make R2 the one home for every archived object, and upload it from CI | Merged. Remote branch auto-deleted |
| #324 | Let the upload job's manifest commit reach main | Merged. Remote branch auto-deleted |
| #331 | Scope the upload job's app token to whichever repository runs it | Merged `7639968`. Remote branch auto-deleted |

In `rewire-evidence`: `main` only, two commits plus the CI commit, pushed, CI green. **Zero open pull requests in either repository.** Nothing of mine needs merging, finishing or dropping.

Do not attribute by branch prefix: both sessions use `claude/`. #327 and #330 were the other session's, not mine.

### Worktrees

I created and removed two (`wt/drift-ids-20261120`, `wt/token-scope-20261120`). Ten worktrees remain and **none were created by this session**: `wt/ag-evidence`, `wt/claims-arch-20261117`, `wt/db-schema5`, `wt/discrepancy-v4`, `wt/enchant-v3`, `wt/entities-20261115`, plus `rewire-benchmark-data-links`, `-utr`, `-vepfu` and one under another session's scratchpad. `wt/claims-arch-20261117` has my now-merged `claude/r2-single-home` branch checked out and can be moved to another branch safely.

Note: `node_modules` in `wt/drift-ids-20261120` was a symlink to a sibling worktree's copy, to avoid a second 89 MB install. That worktree is gone. If you do the same, use a symlink and not a hardlinked clone.

### Background jobs

All finished. No monitors armed, no background shells running.

The three throwaway clones from the Lane 6 work (`l6-dry`, `l6-dry2`, `l6-real`, 2.2 GB between them) have been deleted. Nothing depended on them: `l6-real` was the exact object set already pushed to `-next`, and the cutover re-filters from a fresh clone regardless. Other directories remain in that session scratchpad from earlier in the day; it is session-scoped temporary space and nothing in this repository or the data repository references it.

---

## 5. The protected files, and the invariants that must hold

I changed five of these today, so this is first-hand rather than inferred.

### `shared/omics/reports/figures.ts` and `tests/omics-report-drift.test.ts`

**Invariant: the drift check must never be positional.** `records add` keeps every store file sorted by ID and `validateRecords` refuses one that is not, so a new record lands wherever its ID sorts and shifts every line below it. A check that hashes the first N lines reads that as a changed record. It did: an intake of 116 new sources that touched nothing failed as `a measured record changed`.

So each data input records one line per measured record, its ID and the digest of that line, in `maintenance/reports/span-resolution-figures-records.tsv` beside the figures. A record that is gone fails, a record whose bytes changed fails, a file that gained records passes.

- The sidecar must stay **sorted the way the store sorts**, or diffs become unreadable and git deltas stop being small. It is 56,364 lines, 4.5 MB, 1.07 MB packed.
- `readFigures` folds it in, `writeFigures` splits it out. Do not put the records in the JSON.
- The layer registry has **no `id`**, so its rows are keyed on their own line digest. An edited registry row therefore reads as *gone* rather than *changed*. It still fails, which is what matters.
- Three tests exist specifically to prove the check was not made quieter: a record sorting into the middle is not a change, a changed record fails naming it, a removed record fails naming it. **Keep all three.** Without them the review question is unanswerable.

### `scripts/archive/**` and `shared/archive/**`

**Invariant: R2 is the one home of every object; a release asset is a mirror.** `store` names the home, `mirrors` names other backends holding a copy. Four schema rules hold it honest, and two of them have a trap in them:

- A restricted row never names a public release as home or mirror. **This rule must not apply to a removed row**, whose `store` is a historical note about bytes that no longer exist. Applying it makes a published object untakedownable.
- A removed row names no mirror, so `markRemoved` **must clear `mirrors`**. Without that, a row that had gained `github-release` as a mirror could not be taken down at all. Takedown has to work whatever else a row says.
- `markHome` and `markMirror` return whether they **actually wrote**, so a summary counts rows it moved rather than rows it looked at.
- **An upload marks a row whose object it finds already in the bucket.** This is not an optimisation. Without it, a run interrupted between the PUT and the manifest commit can never be repaired: the next run finds the object present, skips it, and leaves the row naming a release tag for good. This exact repair was needed today.
- `publish` promotes a `local-cache` row to `github-release` as its **home**, not a mirror, because a laptop directory is a home no other reader can reach. Once the bucket is the home the tag goes back to being a mirror.
- `t5-no-store` is refused by name, first, before any visibility check, so a takedown holds whatever the tier says and whatever order commands run in.
- `pull` keeps the compressed bytes it received and never recompresses: gzip is not byte-stable across zlib versions.
- `pull --from release` exists for one job, filling a cache so objects can be put *into* the bucket, and needs no credential. It selects only rows a release tag holds and counts the rest, split into "already in the bucket" (fine) and "no remote copy" (one copy on one machine, the number that matters).

### `maintenance/reports/**`

The figures file and its sidecar are a matched pair. `span-resolution-figures.json` holds figures measured on a tree; the sidecar holds the records they were measured over. Changing one without the other is the drift the whole mechanism exists to catch. Re-measure with `npm run spans -- measure` rather than editing either by hand.

### Not touched by me

`docs/record-contract.md`, `docs/collection.md`, `docs/adr/**`, `data/vocab/**`, `data/omics/checks-baseline.json`. The only thing I know about them: determination 6 means `docs/collection.md`'s review step has to be rewritten, because there is no human review anywhere.

---

## 6. Things that are true and were not written down

**Byte identity**

- Python's default JSON writes `1e-05` where JavaScript writes `0.00001`. This breaks exactly four records in the live store, on fields `tol` and `learning_rate`. Reproducing ECMAScript `Number::toString` is the fix.
- Above 2^53 they disagree the other way: JavaScript writes the shortest round-tripping digits padded with zeros (`123456789012345680000`), Python's `int()` writes the exact binary value (`123456789012345683968`). **The 93,216-record oracle cannot catch this**, because no record holds a number that large. `tests/js-fixture.json` is output Node actually produced, committed rather than described, and it is the only reason that bug was found.
- **Key order is load-bearing**, and it is subtler than it looks. `recordSchema.parse()` normalises top-level key order to the schema's declaration order, so a non-TypeScript producer of a `records add` batch cannot change identity that way. It does **not** normalise keys inside `attributes`: reversing them on `2ome-lm-2025` produces different bytes. Any external producer must emit nested keys deterministically, or a re-export of unchanged content reads as a change. Never retro-sort `attributes` on existing records.
- Numbers inside a batch are safe, because the value round-trips through a double and TypeScript writes the canonical form at write time.

**Traps that cost real time today**

- **`npm run` writes its banner to stdout.** Redirecting a `--json` summary to a file captures two banner lines and `require()` throws on the first character. Use `npm run -s`. Three independent reviewers found this in my workflow; it would have failed after a 900 MB download.
- **`actions/checkout` persists the default token as an `http.extraheader`**, and that beats credentials set on a remote URL. A push then goes out as `github-actions[bot]` and is refused by branch protection. `persist-credentials: false` is the fix. This cost one full upload run: all 871 objects transferred, then the manifest commit was lost to five 403s.
- **`actions/create-github-app-token` is scoped by the names in the workflow**, not by where the app is installed. An org-wide install does not widen a scope the workflow narrowed.
- **A boolean workflow input arrives as the string `false`**, which is not empty, so `${VAR:+--flag}` passes the flag either way.
- `uv run` needs `--no-project` when run inside a directory that has its own `pyproject.toml` but you want an unrelated dependency.
- `timeout` is not on this macOS shell.
- Nested heredocs inside a `gh pr create --body "$(cat <<EOF ...)"` can trip the shell-safety check. Write the body to a file and use `--body-file`.

**Slow commands**

- **Never run the full vitest suite locally.** Run single files with `--pool=forks --poolOptions.forks.minForks=1 --poolOptions.forks.maxForks=1`.
- A fresh mirror clone of the data repository is 4.44 GiB and takes many minutes. `filter-repo` over it takes seconds by comparison.
- `tests/archive-fetch.test.ts` takes about 3 seconds; the rest of the archive tests are tens of milliseconds.
- The `spans -- measure` run is 30 to 60 seconds and needs the object cache, which is gitignored, so it cannot run on a clean clone. That is why the drift test compares digests instead of re-measuring.

**Measurement traps**

- **Uncompressed blob sizes lie about clone cost.** Uncompressed, the release bundles are 14.8% of history and the store's own JSONL dominates. Packed, the JSONL deltas to almost nothing (493 versions of the provenance records pack to 5.1 MiB) and the bundles are 87.2%. I nearly concluded Lane 6 was not worth doing from the wrong one.
- **A figure is only meaningful with the commit of the store it was measured against.** The two checkouts on this machine differ: main had 90,234 provenance rows at one point where a branch had 93,206, and today main has 93,216. Always state the commit.
- **Span resolution is 35.7%, not the 21.7% I had in my head from an older report.** 18,667 of 52,278 resolve, and 91.6% of those print the stored value exactly. Read `maintenance/reports/span-resolution-figures.json`, not any prose.

---

## 7. Promised or asked for, and not done

1. **The label-stage acceptance check.** This is the biggest open item and it is a decision, not a task. Measured today on the first live run (147 questions, 0 transport failures): warm 15 of 66 answerable tables correct, cold **0 of 43** with 5 wrong. The rule

   ```
   confidence !== "high" || quoteNamesRecord(quote, names)
   ```

   discarded 23 answers, of which **22 named exactly the right record** (17 of 17 warm, 5 of 6 cold). It therefore costs 22 correct labels to prevent one wrong one, and because a quote must come from the caption, it can only be satisfied when the record's name echoes the caption, which is the circularity it was meant to prevent. I proposed removing it and re-measuring. **The owner did not approve or refuse**, and I did not touch it. Removing it is predicted to take warm from 15 to 32 of 66 and cold from 0 to 5 of 19.

2. **Re-measuring the label stage after that change.** Cost is about 300k input tokens per full run, roughly £6. The loop is cheap now; use it.

3. **The deeper labelling finding, which no amount of retrieval fixes.** For a genuinely new paper the right record usually does not exist. Removing paper families, only 370 of 2,242 assessment records are cited by two or more families; of 66 held-out tables, 2 would still have their truth record, 6 have a surviving parent, 58 have nothing. So the stage's job is **minting**, not retrieval. The draft-graph path in `rdf-kg-mcp` is the mechanism for it. An earlier 71.2% retrieval figure of mine was leakage and is withdrawn; it is corrected in the plan file and the published report.

4. **The status report artifact is private.** `https://claude.ai/artifact/4drzQVPAYbyVqo2zrhsoEF`. It needs sharing from its own Share menu before anyone else can open it.

5. **Lane 6 beyond the history**, as listed in section 2: assets, settings, lock repoint, issue transfer, archive, rename. None done, all deliberate.

6. **Nothing was promised to the owner and silently dropped.** Items 1 and 2 are the only things I said I would do and did not, and both stopped on a decision rather than on effort.
