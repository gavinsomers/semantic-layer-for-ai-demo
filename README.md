# Yellowhammer: a semantic layer for AI you can run on your laptop

This repository is the runnable companion to two publications by Gavin Somers:

- the book *Your AI Is Guessing*, which argues that an AI assistant should call recorded business definitions instead of writing its own formulas;
- the free guide *Building the Semantic Layer for AI: An implementation guide for analytics engineers*, which shows how to build that, step by step.

Everything runs locally: a DuckDB warehouse built with dbt (dbt Core 1.12, in dbt's latest YAML spec), a MetricFlow 0.213 semantic layer, an MCP server, a response contract, a question router, a SQL guard and per-user access control. You can try all of it without a model server. With a local model (Ollama, vLLM, LM Studio or any OpenAI-compatible server) you can also watch a governed assistant and a raw-SQL "wrapper" answer the same question side by side.

The data is Yellowhammer, the book's fictional furniture and lighting retailer. The book's Friday review figures are exact: £50 of provisional contribution after advertising on paid search under the paid-refund rule, £0 under the approved-refund rule, £1,200 of cash received. Everything else is synthetic and marked as such (see "Which data is from the book").

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.12 and every package for you)
- git
- Optional, for the live steps: a model server that speaks the OpenAI chat API with tool calling, for example [Ollama](https://ollama.com/) or vLLM

Works on Linux, macOS and Windows. `uv run` always uses the project's own `.venv`, not whichever Python or virtual environment your shell has active.

## Quick start (no model server needed)

```
git clone <this repository> semantic-layer-for-ai-demo
cd semantic-layer-for-ai-demo
uv sync
uv run python -m sl build
uv run python -m sl ask --offline --user all "What did we make on paid search in the Friday review?"
```

The last command asks the same question as three users. The finance lead and the commercial lead get £50 (furniture £120, lighting −£70); the lighting buyer gets −£70, labelled as limited to lighting rows. Each answer comes with its filters, cutoff, caveats, lineage (metric, version, owner) and the SQL MetricFlow compiled.

On Linux and macOS, `./sl <command>` is a shortcut for `uv run python -m sl <command>`. Run `uv run python -m sl` with no arguments to list every command.

More things to try offline:

```
uv run python -m sl test                                       # dbt tests and the Python tests
uv run python -m sl ask --offline "What was our profit on paid search last week?"   # a named-choice question
uv run python -m sl ask --offline "Why did lighting fall?"     # refused before any query runs
uv run python -m sl ask --offline --user commercial_lead "How much cash came in for the paid-search orders in the Friday review?"
uv run python -m sl ask --offline "How many orders used more than one promotion?"   # flagged SQL fallback
uv run python -m sl route "What if we count approved refunds?"  # how the router classifies a question
uv run python -m sl explain contribution_after_ads --group-by product_group --cutoff friday_review
uv run python -m sl prove-fan-out                              # a naive join fails the build
uv run python -m sl prove-lamp-test                            # the wrong refund rule fails the build
uv run python -m sl audit                                      # who asked what, and which version answered
uv run python -m sl ask --offline --as-of 2026-10-06 "What was paid-search contribution for the trial's first four days?"   # version 2 is in force
```

Building, testing and running the layer (guide chapters 5, 6, 8, 9 and 10), still offline:

```
uv run python -m sl evals --offline --smoke     # ch 8: the 8-case pull-request subset, scored
uv run python -m sl evals --offline             # ch 8: all 60 cases (SQL variants replay the last live run)
uv run python -m sl change-demo                 # ch 9: change a formula in place (CI fails), then ship v2
uv run python -m sl diff                        # ch 9: classify metric changes against production
uv run python -m sl draft --offline             # ch 5: a drafted semantic model fails a gate, then passes
uv run python -m sl check-lock                  # ch 5: no reviewed file changed without review
uv run python -m sl regress                     # ch 6: metric values on frozen data
uv run python -m sl telemetry-demo              # ch 10: a simulated week, one cluster promoted
uv run python -m sl ranges                      # ch 10: each metric's usual daily range (unusual values)
uv run python -m sl evals --offline --smoke --write-baseline   # ch 8: at a release, record the gate's baseline
uv run python -m sl ci                          # ch 6: every CI step, as GitHub Actions runs them
```

## With a local model

By default the live commands use a server at `http://localhost:8001/v1` and the first model it lists. To point elsewhere:

```
export LLM_BASE_URL=http://localhost:11434/v1   # Ollama, for example
export LLM_MODEL=qwen3:32b                       # optional
export LLM_API_KEY=...                           # optional; local servers ignore it
```

(On Windows PowerShell: `$env:LLM_BASE_URL = "http://localhost:11434/v1"`.) The code reads only these `LLM_*` variables, never `OPENAI_*`, so it cannot use a paid account by accident. Pick a model that supports tool calling.

```
uv run python -m sl ask --user all --show-tools "What did we make on paid search in the Friday review?"
uv run python -m sl compare "What did we make on paid search in the Friday review?"
uv run python -m sl mcp --user lighting_buyer       # the MCP server, on stdio, for any MCP client
```

`compare` is the book's core argument in one command. The governed assistant answers through the router, the semantic layer and the response contract. The wrapper is the same model with the raw tables (as recorded by the Friday cutoff) and a SQL tool. In the recorded run of 21 October 2026 (`runs/2026-10-21-compare-qwen3.8-27b-nvfp4.txt`, model `qwen3.8-27b-nvfp4`, NVIDIA's NVFP4 quantisation of the official Qwen3.8-27B, on vLLM) the wrapper deducted the lamp's approved but unpaid £50 refund, called the result profit and answered £0, exactly the mistake the book describes; the governed assistant answered £50 (furniture £120, lighting −£70) with its basis. Wrapper runs are saved under `runs/`.

With a model server, `sl evals --workers 4` runs the full comparison (about 50 minutes for 240 conversations on one local GPU serving four at a time), and `sl draft` has the model draft a semantic model and a second call critique it. In the recorded live draft (`authoring/recorded/2026-10-21-qwen3.8-27b-nvfp4.md`), both of the model's drafts failed the parse gate: it declared `non_additive_dimension` on refund amounts, first as a bare name and then with the legacy spec's `window_choice`/`window_groupings` keys, which dbt's latest spec rejects. Nothing reached review: the gates did their job, and a person decides what happens next. (The earlier draft with a community fine-tune, in the legacy spec, is kept in `authoring/recorded/history/`.)

## Evaluation results (guide chapter 8)

One live run, 21 October 2026: `qwen3.8-27b-nvfp4`, NVIDIA's NVFP4 quantisation of the official Qwen3.8-27B (Hugging Face `nvidia/Qwen3.8-27B-NVFP4`), on vLLM (one GPU, four questions in flight, temperature 0). All 60 cases, the same model and scorer for every variant. Before the first run that included the held-out cases, the hash of `context/`, `serving/router.py` and `semantic_layer.yml` was recorded; it is unchanged in every run of this table, so these are **blind** results: the result of record. Full results, with metadata, consistency, staleness and leaks: `evals/results/2026-10-21-qwen3.8-27b-nvfp4.json` and `.md`. How the cases were written and scored: `evals/README.md`. The gaps the blind run found were fixed afterwards, which spends the held-out set; the rerun is the second table below, labelled "after fixes, NOT blind".

**Blind (result of record)**

| | raw_schema | annotated | governed | hybrid |
|---|---|---|---|---|
| All 60 cases | 25 (42%) | 52 (87%) | 48 (80%) | 50 (83%) |
| Dev split, 40 cases | 12 (30%) | 35 (88%) | 38 (95%) | 39 (98%) |
| of which the spent first held-out split, 13 | 4 | 10 | 12 | 12 |
| Held-out split (blind), 20 cases | 13 (65%) | 17 (85%) | 10 (50%) | 11 (55%) |
| Supported, 24 (held-out 9) | 20 (9) | 23 (9) | 20 (5) | 21 (6) |
| Constrained, 21 (held-out 6) | 4 (4) | 17 (4) | 14 (1) | 15 (1) |
| Unsupported, 15 (held-out 5) | 1 (0) | 12 (4) | 14 (4) | 14 (4) |
| Value correct, where a figure was expected (40; held-out 14) | 30 (13) | 38 (13) | 32 (8) | 33 (8) |
| Access runs that leaked, every run (single pass and repeats) | 8 of 21 | 0 of 27 | 0 of 27 | 1 of 27 (see below) |
| Injection case obeyed, every run | 4 of 5 | 3 of 7 | 0 of 7 | 0 of 7 |
| Consistency: identical on every run (15 cases; 6 runs each, raw_schema 4) | 7 (47%) | 5 (33%) | 13 (87%) | 12 (80%) |
| Consistency: the figure itself varied | 7 (47%) | 8 (53%) | 1 (7%) | 2 (13%) |
| Consistency: the behaviour varied | 2 | 2 | 1 | 2 |
| Staleness, clock moved past version 2's start (15 runs) | n/a | 3 | 15 | 15 |
| Staleness, version 2 brought forward unannounced (15 runs) | n/a | 3 | 10 | 8 |
| Mean tokens per answer | 20,675 | 7,630 | 4,863 | 5,381 |
| Mean seconds per answer | 102.1 | 35.8 | 20.1 | 23.9 |
| Answers via SQL fallback | n/a | n/a | n/a | 10% |

The consistency subset is 15 cases chosen before the run (a mix of categories and traps, five of them held-out), each run again from scratch: five repeats (three for raw_schema) plus the single pass. The vLLM server crashed during the third repeat (a CUDA illegal memory access, seven questions in flight against four slots); after a restart the failed runs and the remaining repeats were resumed with the frozen code, two in flight, so every case has all its runs. Temperature 0 does not make vLLM deterministic, and the table measures by how much. The one hybrid "leak" is the keyword check's false positive: on one repeat the lighting buyer was given lighting's own contribution at the September close, −£120, and the check forbids the number 120 (furniture's Friday figure); no furniture or cash figure was shown. The staleness cases are `evals/stale_cases.yml`: five questions on current contribution, the lamp's group (lighting), the comparison, and the Friday review as a control.

What it shows, plainly:

- **Accuracy does not favour the layer on unseen questions.** On the blind held-out set, annotated context scored 85% and the governed variants 50 to 55%, below the bare schema (65%). Half of governed's ten held-out failures are real errors: a question about one product (sofas, dining chairs) got the all-product stock total because the router has no product filter; "as a comparison, ... on Friday" got version 1 (£50) instead of the approved-refund comparison (£0); "has the £50 moved?" lost its explanation because the figure check withheld the model's wording for quoting the user's own £50; and "contribution after ads for September" was answered instead of clarified (all four fixed since: see the after-fixes table). The other half are the blind writer's judgement calls against the layer's design, listed in `evals/README.md`: refunds by payment date (£245) where the governed metric uses the order date (£95); a labelled subtotal for an unknown cost, where the layer refuses; an answer for promotion orders, where governed has no SQL; "profit ... going by the Friday review" answered with a caveat, where the router asks. One case (h2_18) fails the same way for annotated, governed and hybrid: the answer declined furniture and cash and gave lighting's −£70, which the writer allows, but the case expects a refusal record. The dev split (95 to 98% for governed and hybrid) is the tuned score and says little.
- **Consistency and access are where the layer earns its place.** On the same 15 questions, governed gave the same behaviour and figures on all six runs for 13 of 15 and hybrid for 12; annotated for 5 and the bare schema for 7, with the figure itself changing between runs on 8 and 7 questions (annotated gave September refunds as £245 on some runs and £95 on others, and "has the £50 moved?" as £0 or £420; the bare schema gave the Friday contribution as £50 or £0). No governed or annotated answer showed a figure its user may not see, and hybrid's one flagged run is a false positive (above); the bare schema leaked in 8 of 21 access runs (customer emails, the lighting buyer's cash and furniture) and obeyed the injection in 4 of 5 runs. Annotated obeyed it in 3 of 7.
- **A hand-written context file goes stale; the layer mostly does not, but its context can.** When the clock moved past version 2's published start, annotated still answered every current-contribution question under the old rule (£50, lighting −£70) and called the approved-refund rule not yet in force, while governed and hybrid switched to version 2 (£0, lighting −£120) and still reproduced the Friday review's £50. When version 2 was brought forward with no hand-written file updated, governed and hybrid also gave the old figure on most current-contribution runs: the model picked version 1 because `context/instructions.md` (hand-written, not regenerated) still says it is in force until 5 October. The layer then attached its own warning that version 1 is no longer the rule for current answers, but did not swap the metric. Annotated gave the old figure with no warning. (Fixed since: the layer now binds the version in force, and the instructions no longer state a date; see the after-fixes table.)
- Governed and hybrid used about a quarter of the bare schema's tokens per answer and 64 to 71% of annotated's, and answered faster.

**After fixes, NOT blind** (`evals/results/2026-10-21-qwen3.8-27b-nvfp4-after-fixes.json` and `.md`). The same model, server and scorer, after the fixes listed under "Known limitations" below; two questions in flight. The fixes were written with the blind failures in view, so this measures whether they work, not how the layer does on questions nobody has seen. raw_schema and annotated do not use the layer: their changes are run-to-run variation.

| | raw_schema | annotated | governed | hybrid |
|---|---|---|---|---|
| Held-out split, 20 cases (blind run in brackets) | 12 (13) | 16 (17) | 12 (10) | 13 (11) |
| Supported, 9 | 8 | 8 | 5 | 6 |
| Constrained, 6 | 4 | 3 | 3 | 3 |
| Unsupported, 5 | 0 | 5 | 4 | 4 |
| Value correct, where a figure was expected (14) | 12 | 11 | 10 | 9 |
| Access runs that leaked (4 cases, 1 run each) | 2 of 4 | 0 of 4 | 0 of 4 | 0 of 4 |
| Staleness, clock moved past version 2's start (15 runs) | n/a | 3 | 14 | 15 |
| Staleness, version 2 brought forward unannounced (15 runs) | n/a | 3 | 15 | 15 |
| Answers via SQL fallback | n/a | n/a | n/a | 8% |

The fixed cases now pass for governed and hybrid: one product (sofas, dining chairs: 0 and 16), and "contribution after ads for September" asks which whole advertising period is meant. Version binding closed the unannounced-version gap: with version 2 brought forward and no hand-written file updated, governed and hybrid gave the current figure on every run (10 and 8 of 15 blind); annotated still gave the old one. The clock run's one governed miss is the Friday review given as £50 without its product-group split. Still failing, honestly: "as a comparison, ... on Friday" (h2_09) now routes to version 2, but the governed model also queried version 1 and the answer took the last figure (£50), and neither variant gave the product-group split the case expects; "has the £50 moved?" (h2_11) keeps its wording now that the figure check reads the user's £50 correctly, but the wording names none of the phrases the case checks for (hybrid queried advertising instead); one governed answer used the September close for the trial's goods value (h2_01, passed blind); and the writer's judgement calls (h2_04, h2_05, h2_10, h2_13, h2_18) are unchanged by design.

Offline, `sl evals --offline` re-runs governed and hybrid for real and re-scores the SQL variants from this recording. The previous live run (20 October 2026, a community fine-tune, 40 cases) is kept, labelled, in `evals/results/history/`.

To use the MCP server from a desktop client, register the command `uv run --directory /path/to/semantic-layer-for-ai-demo python -m sl mcp --user finance_lead`. The server is started for one signed-in user, a stand-in for OAuth: the identity comes from the session, never from a tool argument. There is no default user: without `--user` or the `SL_USER` environment variable the server refuses to start.

## What this shows

| What you see | Run | Book chapter | Guide chapter |
|---|---|---|---|
| A wrapper writing its own SQL picks a refund rule nobody approved | `sl compare "..."` | 1 The assistant that was sure | 1, 4 |
| Entities, keys, one primary entity per model, a customers dimension | `dbt/models/semantic/semantic_layer.yml` | 3 What a semantic layer is | 2 |
| A many-to-many link table and the tests that catch its fan-out | `sl prove-fan-out` | 3 | 2 |
| The lamp line as the test of the refund rule | `sl prove-lamp-test` | 1, 3 | 2 |
| Simple, ratio, cumulative, derived and semi-additive metrics | `sl metrics`, `sl explain ...` | 3 | 2 |
| Amounts stored positive with a direction; unknown costs kept unknown | `dbt/tests/` | 3 | 2 |
| Facts carry `recorded_at`; every answer names its cutoff | `sl query contribution_after_ads --cutoff lamp_recalculation` | 5 What was known when | 2 |
| Owner, approver, status, version and last review on every metric | `sl metrics` | 2 Find the rules nobody wrote down, 9 | 2, 9 |
| Descriptions, synonyms, verified queries, instructions, ambiguity rules | `context/` | 4 | 3 |
| Supported, constrained and unsupported questions, routed before any query | `sl route "..."` | 4 What the assistant may ask for | 3, 4 |
| The response contract: value, filters, cutoff, caveats, lineage, SQL, path | `sl ask --json ...` | 4, 7 What the answer may claim | 4 |
| Governed metric first, read-only SQL fallback flagged | `sl ask "How many order lines had more than two units in September?"` | 4 | 4 |
| MCP tools: list_metrics, get_dimensions, query_metrics, run_readonly_sql | `sl mcp` | 4 | 4 |
| Same question, different users, correctly scoped answers, audit log | `sl ask --user all ...`, `sl audit` | 6 Who may see it | 7 |
| An agent drafts a semantic model; parse, sample questions, critic, then a person | `sl draft --offline` | none | 5 |
| A hash lockfile: an unreviewed edit to a metric or context file fails CI | `sl lock`, `sl check-lock` | none | 5 |
| The CI pipeline: build, validate, tests, eval smoke set, lock, breaking-change gate | `sl ci`, `.github/workflows/ci.yml` | 8 When the meaning changes | 6 |
| Metric regression test on frozen data | `sl regress`, `ci/expected_metrics.yml` | 8 | 6 |
| 60 questions, three categories, a blind held-out split, planted traps, one scorer; repeats, staleness, leaks | `sl evals`, `evals/cases.yml`, `evals/README.md` | 4, 10 From the wrapper you have to the foundation you need | 8 |
| Raw schema vs annotated context vs governed vs hybrid, same model | `sl evals` (live) | 1, 4 | 8 |
| Manifest diff: formula, filter, grain, rename, added, removed, metadata-only | `sl diff`, `sl change-demo` | 8 | 9 |
| v1 deprecated beside v2, with a sunset date and a notice the agent passes on | `sl change-demo` | 8, 9 Many assistants, one meaning | 9 |
| Code owners on metric files; a PR template with business sign-off | `.github/` | 2, 9 | 9 |
| Telemetry per answer: path, metric and version, latency, tokens, feedback | `logs/telemetry.jsonl`, `sl telemetry-report` | 9 | 10 |
| Bypass rate, unused metrics, fallback clusters, drift, freshness, alerts | `sl telemetry-demo` | 9 | 10 |
| Templates: metric metadata, verified query, test case, PR, telemetry schema | `templates/` | none | Appendix C |

## Layout

```
sl.py, sl                one command for every step (python -m sl; ./sl on Linux and macOS)
dbt/                     guide ch 2: seeds, staging, marts, semantic layer, tests
  seeds/seeds.yml          which data is from the book and which is synthetic
  models/marts/            fact tables at "one row per thing per cutoff"
  models/semantic/         semantic models and metrics (dbt's latest YAML spec: on each mart's
                           entry, with its column tests), with operating-agreement meta
  macros/uk_time.sql       UK dates from UTC instants, in one place
  tests/                   the Friday figures, fan-out, sign and null-rule tests
context/                 guide ch 3: verified queries, synonyms, instructions, ambiguity rules
serving/                 guide ch 4 and 7
  layer.py                 wraps mf list / mf query --explain / mf query --csv
  clock.py                 the demo clock (2 October 2026) and UK time
  unusual.py               unusual-value caveats against expected_ranges.json (`sl ranges`)
  router.py                supported / constrained / unsupported, before any query
  contract.py              the response contract, checked against response_contract.schema.json
  sql_guard.py             read-only checks, allow-lists, row filters and LIMIT, with sqlglot
  tools.py                 the four tools, each with the caller's identity
  mcp_server.py            the same tools over MCP (FastMCP, stdio)
  agent.py                 the governed assistant (live model or --offline)
  wrapper.py               the raw-SQL wrapper, for comparison
  users.yml, access.py     who may see what, and the audit log (logs/audit.jsonl)
authoring/               guide ch 5: AI-drafted semantic models and their gates
  draft.py                 draft, parse and validate, sample questions, critic, person
  canned/                  the offline drafts: round 1 fails a gate, round 2 passes
  lock.py, reviewed.lock.json   hashes of reviewed files; CI fails on an unreviewed edit
ci/                      guide ch 6: metric regression on frozen data (expected_metrics.yml)
.github/                 guide ch 6, 9: CI workflow, CODEOWNERS, pull request template
evals/                   guide ch 8: the evaluation harness
  cases.yml                60 cases: supported, constrained, unsupported; dev and a blind held-out set
  stale_cases.yml          the staleness test's cases (`sl evals --stale-context`)
  README.md                splits, the freeze, the held-out writer's judgement calls, the measures
  scorer.py                one scorer for every variant
  baseline.json            the last release's scores: the CI gate fails on a fall
  run.py                   raw_schema, annotated, governed and hybrid, with the same model
  context/annotated.md     the annotated-schema context for the SQL variants
  results/                 every saved live run, JSON and a markdown summary (history/: older models)
change/                  guide ch 9: semantic manifest diff, impact, versioning
  manifest_diff.py         classifies each change and flags the breaking ones
  impact.py, consumers.yml affected verified queries, eval cases and consumers
  demo.py                  the gate, `sl release` (and its CHANGELOG.md entry), and the change demo
  production_manifest.json what is in production (the diff's default base)
telemetry/               guide ch 10: log schema, simulated week, weekly report
templates/               guide Appendix C
tests/                   pytest: every chapter, including evals, change, authoring, telemetry, CI
```

The folders follow the guide's Appendix D: models and semantic definitions
(`dbt/`), verified queries and context (`context/`), the MCP server and
serving path (`serving/`), evals (`evals/`), CI (`.github/`, `ci/`) and
monitoring (`telemetry/`).

## Known limitations, kept on purpose

The four held-out failures from the run of 20 October 2026 are fixed and tested (revision 2): "margins" now folds to "margin" (the router stems plurals and common inflections before any rule); a stock position "at the Friday review" is the one recorded by its cutoff (48, on 4 September); "across all channels" leaves the default channel off (u09 is refused for its unknown cost); and a qualifier no request can apply ("orders that used a promotion") goes to the flagged SQL fallback, or is refused with SQL off, never answered with a wider governed figure (c15). Fixing them spent that held-out split (now dev, `spent: true`).

The blind held-out set that replaced it found new gaps. They were fixed after its result was recorded, which spends that set too (`evals/README.md`; tests in `tests/test_heldout_fixes.py`):

- Product filters: products are filter values (`context/synonyms.yml`), each metric's own product dimension is found from the manifest, and a filter the question pins down (one value named) is applied even when a model's request leaves it out.
- Version binding: a current question answered with a superseded version gets the version in force, with a caveat, unless the question names the report or the old rule. `context/instructions.md` no longer states a version date; the prompt carries a line generated from metric metadata.
- The figure check read "£50" as 350 (JSON's `\u00a350`) and withheld wording that quoted the user's own figure; fixed.
- MetricFlow's column names (a time dimension comes back with its grain) no longer raise a KeyError.
- A window that cuts through an advertising period (recorded whole, never split by day) gets a named-choice question; the advertising model gains `period_end`.
- The approved-refund rule is recognised in any word order; a bare follow-up ("And for furniture?") asks which figure instead of going to the SQL fallback.

Still open: when the model queries two metrics, the answer takes the last one (h2_09's governed run answered £50 after querying £0 first); the router has no day-level date parser ("Friday 4 September" reads as September).

Kept for other reasons:

- `order_promotions` and `cost_estimates` have no `recorded_at`, so they cannot be cut back to what was known at a cutoff.
- The stock metrics' `non_additive_dimension` has no `window_groupings`: a position is taken on the last day of the window across all products, not per product.
- Qualifiers the router knows are listed in `context/synonyms.yml` (`qualifiers:`); a narrowing it does not know is still missed. The figure check marks signs only within a short distance of the number.
- The routing that keeps SQL second classifies the `question` a client sends; an MCP client's model can misstate it. The identity, row filters, column allow-list and the read-only connection hold whatever is sent; in production, so must the warehouse role.
- `goods_value_mtd` gives one allowed warning: dbt 1.12 also writes an Open Semantic Interchange document (`target/osi_document.json`), which cannot hold a cumulative metric's `grain_to_date`. MetricFlow's answers are unaffected. `sl validate` lists it in `ALLOWED_WARNINGS`; any other warning fails CI.
- DuckDB's `icu` extension (time zones) is downloaded once, on the first `sl build`.

## Which data is from the book

From the book: the paid-search orders accepted from Monday 31 August 2026 00:00 up to, not including, Friday 4 September 00:00, with the cutoff of 06:00 that Friday; their totals (goods value £1,500, paid refunds £100, estimates £930, advertising £420: furniture £300, lighting £120); the results (£50 under the paid-refund rule: furniture £120, lighting −£70; £0 under the approved-refund rule); cash received £1,200 (£1,300 collected less £100 refunded); the lamp line (two lamps, £200 of goods, £80 and £20 of estimates, a £100 refund paid before the cutoff and a £50 goodwill refund approved but unpaid, paid on Tuesday 8 September at 11:00 and recorded at 12:00); the £0 recalculation at the 11 September cutoff; and the sofa stock position at 05:00 on 4 September (four on hand, three reserved, one quarantined, none available).

Illustrative: how those totals split across individual lines, orders and payments; the exact times of the lamp refunds' approval and first payment; customer ids on the book's orders.

Synthetic: every customer, every order numbered YH-2xxx (email and organic, August and September 2026), promotions and the `orders_with_promotion` metric, the other refunds, the other stock observations, the `september_close` cutoff, the access rules in `serving/users.yml`, the consumers in `change/consumers.yml`, the drafted refunds model in `authoring/`, and all telemetry traffic (`sl telemetry-demo` simulates two weeks; no Yellowhammer assistant traffic exists). The deprecation in `sl change-demo` follows the book: the approved-refund rule becomes the rule for current answers from Monday 5 October 2026, and the paid-refund version stays to reproduce the Friday review's £50; it runs in a scratch copy, so the project itself still has version 1 in force, as on Friday 2 October (the demo clock; with `--as-of 2026-10-06`, current answers use version 2). The book gives no figures for paid search after the trial's first four days, so none are invented here. Any figure the warehouse computes beyond those listed above is not a fact from the book.

Times are stored as instants in UTC with their offset (`TIMESTAMPTZ`); cutoffs are defined as UK clock times with their zone (`dbt/seeds/cutoffs.csv`, converted in `stg_cutoffs`: 06:00 BST on 4 September 2026 is 05:00 UTC); every time shown is UK time with its zone. The demo's "today" is fixed at Friday 2 October 2026 (`serving/clock.py`); `--as-of` moves it for one command.

## Licence

The code and the templates in `templates/` are MIT licensed (`LICENSE`, copyright (c) 2026 Gavin Somers): copy them into your own project. The text of the guide *Building the Semantic Layer for AI* and of the book is licensed separately, under CC BY-NC-ND 4.0 for the guide; neither is part of this repository.
