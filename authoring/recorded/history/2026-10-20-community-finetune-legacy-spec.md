> **Historical, 20 October 2026.** Recorded with a community fine-tune of Qwen3.8-27B, not an official model, before revision 2 (40 eval cases; the legacy dbt spec). Superseded by the run of 21 October 2026 with `qwen3.8-27b-nvfp4` (`nvidia/Qwen3.8-27B-NVFP4`); kept only as a record.

# Live run of `sl draft`, 20 October 2026, qwen3.8-27b-uncensored on vLLM (12 minutes).

    Drafting a semantic model for fct_refunds (live model); at most 2 rounds.
    
    Round 1:
      FAIL parse      [0m18:37:40  Unable to do partial parsing because saved manifest not found. Starting full parse. | [0m18:37:41  Encountered an error: | Parsing Error | Invalid semantic_models config given in FilePath(searched_path='models', relative_path='semantic/draft_refunds.yml', modification_time=1792521459.405354, project_root='<scratch>/tmpi3g9pw6i/dbt') @ semantic_models: {'name': 'refunds', 'description': 'Refund snapshots at one cutoff, one row per refund per cutoff.', 'model': "ref('fct_refunds')", 'defaults': {'agg_time_dimension': 'order_accepted_date'}, 'entities': [{'name': 'refund_snapshot', 'type': 'primary', 'expr': 'snapshot_key'}, {'name': 'cutoff', 'type': 'foreign', 'expr': 'cutoff_id'}, {'name': 'order', 'type': 'foreign', 'expr': 'order_id'}, {'name': 'channel', 'type': 'foreign'}, {'name': 'product_group', 'type': 'foreign'}, {'name': 'customer', 'type': 'foreign', 'expr': 'null'}], 'dimensions': [{'name': 'order_accepted_date', 'type': 'time', 'expr': 'accepted_date', 'type_params': {'time_granularity': 'day'}}], 'measures': [{'name': 'refund_amount_outstanding', 'agg': 'sum', 'expr': 'amount_outstanding', 'non_additive_dimension': 'order_accepted_date'}, {'name': 'refund_amount_approved', 'agg': 'sum', 'expr': 'amount_approved', 'non_additive_dimension': 'order_accepted_date'}]} - at path ['measures'][0]['non_additive_dimension']: 'order_accepted_date' is not valid under any of the given schemas
    Round 2:
      FAIL parse      [0m18:45:05  Measure 'refund_amount_approved' has a non_additive_dimension with an invalid 'window_groupings'. These entities {'refund_id'} do not exist in the semantic model. | [0m18:45:05  Encountered an error: | Parsing Error | Semantic Manifest validation failed.
    
    Draft written to authoring/scratch/refunds.yml (not merged).
    The draft did not pass the gates in two rounds: a person must decide what to do.

## The round 2 draft the gates stopped

```yaml
semantic_models:
  - name: refunds
    description: Refund snapshots at one cutoff, one row per refund per cutoff.
    model: "ref('fct_refunds')"
    defaults:
      agg_time_dimension: order_accepted_date
    entities:
      - name: refund_snapshot
        type: primary
        expr: snapshot_key
      - name: cutoff
        type: foreign
        expr: cutoff_id
      - name: order
        type: foreign
        expr: order_id
      - name: channel
        type: foreign
      - name: product_group
        type: foreign
      - name: customer
        type: foreign
        expr: null
    dimensions:
      - name: order_accepted_date
        type: time
        expr: accepted_date
        type_params:
          time_granularity: day
      - name: refund_id
        type: categorical
        expr: refund_id
    measures:
      - name: refund_amount_outstanding
        agg: sum
        expr: amount_outstanding
        non_additive_dimension:
          name: order_accepted_date
          window_choice: max
          window_groupings:
            - refund_id
      - name: refund_amount_approved
        agg: sum
        expr: amount_approved
        non_additive_dimension:
          name: order_accepted_date
          window_choice: max
          window_groupings:
            - refund_id
metrics:
  - name: refunds_outstanding
    label: Refunds Outstanding
    description: >-
      Total refund amount that is approved but not yet paid, in GBP, at one cutoff.
      Grain: refund snapshot per cutoff, aggregated by order accepted date. Includes refunds
      where refund_state = approved; excludes requested-only and paid refunds. Not for:
      counting refund events, paid refunds, or unfiltered multi-cutoff totals.
    type: simple
    type_params:
      measure: refund_amount_outstanding
    config:
      meta:
        owner: "finance-data"
        approver: "Gav Man"
        status: draft
        version: 1
        last_reviewed: "2026-02-22"
  - name: refunds_approved
    label: Refunds Approved
    description: >-
      Total refund amount that is approved, whether paid or not, in GBP, at one cutoff.
      Grain: refund snapshot per cutoff, aggregated by order accepted date. Includes refunds
      where refund_state = approved or paid; excludes requested-only refunds. Not for:
      counting refund events, outstanding refunds, or unfiltered multi-cutoff totals.
    type: simple
    type_params:
      measure: refund_amount_approved
    config:
      meta:
        owner: "finance-data"
        approver: "Gav Man"
        status: draft
        version: 1
        last_reviewed: "2026-02-22"
```
