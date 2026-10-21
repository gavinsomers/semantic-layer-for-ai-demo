# Templates (guide Appendix C)

Copy these into your own project. Each one is the working version used in
this repository, with the Yellowhammer specifics replaced by `<placeholders>`.

| Template | Used here as | Guide chapter |
|---|---|---|
| `metric_metadata.yml` | every metric in `dbt/models/semantic/semantic_layer.yml` | 9 |
| `verified_query.yml` | `context/verified_queries.yml` | 3 |
| `eval_case.yml` | `evals/cases.yml` | 8 |
| `pull_request_template.md` | `.github/PULL_REQUEST_TEMPLATE.md` (identical) | 9 |
| `telemetry_log.schema.json` | `telemetry/log_schema.json` (identical) | 10 |

`tests/test_ci.py` checks that the two copied files stay identical to the
ones in use, and that the YAML templates parse.
