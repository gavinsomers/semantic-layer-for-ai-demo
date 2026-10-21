# Instructions for drafting a semantic model (guide chapter 5)

Both the drafting agent and the critic read this file. It repeats the rules
at the top of `dbt/models/semantic/semantic_layer.yml`, as a checklist a
reviewer can tick. Drafts use dbt's latest YAML spec: the semantic model sits
on the mart's own `models:` entry, entities and dimensions on its columns.

1. One semantic model per mart, with exactly one primary entity at the mart's
   grain. In this project every fact mart has one row per thing PER CUTOFF, so
   the primary entity is on the `snapshot_key` column, never the business id alone.
2. Every fact model carries the `cutoff` entity (on the `cutoff_id` column,
   type foreign). Without it a query cannot be limited to one cutoff and every
   figure is counted once per cutoff.
3. Declare the shared keys as foreign entities with the same names the other
   models use, where the mart has the column: `order` (order_id), `channel`,
   `product_group`, `customer`. Never declare an entity on a column the mart
   does not have.
4. Give the model a time dimension at day grain (`granularity: day` on the
   column) and set it as the model's `agg_time_dimension`.
5. Simple metrics (they replace measures): amounts are positive; use `agg: sum` on an amount column, and
   `count_distinct` for ids. A balance or stock position needs a
   `non_additive_dimension`: never sum a position over days.
6. Every metric has a label and a description written for a language model:
   unit, grain, what is in and out, and a "Not for:" sentence.
7. Every metric carries the operating agreement in `config: meta:` with
   `owner`, `approver`, `status`, `version` and `last_reviewed`. A new draft is
   `status: draft`, `version: 1`, `last_reviewed: null`: nobody has reviewed it
   yet, and approval sets the date. Only a person certifies a metric.
8. Output YAML only: one `models:` entry for the mart, with `semantic_model:`,
   its `columns:` (keep the mart's existing column tests) and its `metrics:`,
   nothing else.
