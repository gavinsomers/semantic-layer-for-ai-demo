# Evaluations (guide chapter 8)

`cases.yml` holds 60 questions; `run.py` asks them under four context variants
(raw schema, annotated schema, governed, hybrid) with the same model, and
`scorer.py` scores every variant's answer the same way. `stale_cases.yml` holds
the five staleness cases. Recorded live runs are in `results/` (older ones in
`results/history/`).

```
uv run python -m sl evals --offline --smoke                    # CI: no model server
uv run python -m sl evals --workers 4                          # all 60 cases, live
uv run python -m sl evals --repeats 5 --cases s01_friday_make u07_commercial_lead_cash
uv run python -m sl evals --stale-context clock                # or: rule
uv run python -m sl evals --split held_out                     # only the blind set
```

## Splits

- **dev** (40 cases: `s*`, `c*`, `u*`). The first evaluation set. Thirteen of
  these were its held-out split; they carry `spent: true` because the author
  saw their failures in the run of 20 October 2026 and changed the router to
  pass them (`CHANGES-REVISION-2.md`, item 10). They are reported as dev, and
  separately as `split:dev, spent held-out`.
- **held_out** (20 cases: `h2_*`). Written blind in revision 2 by someone who
  had seen only the business description, the README's account of the data,
  the seeds and the mart SQL, never `context/`, the router, these cases or the
  semantic models. Every expected value came from independent SQL on a copy of
  the warehouse; the source, with each query, is `research/heldout-v2.yml` in
  the guide repository. `tests/test_evals.py` recomputes each figure from the
  marts.

### The freeze

Before the first run that included the held-out cases, the SHA-256 of
`context/`, `serving/router.py` and `dbt/models/semantic/semantic_layer.yml`
was recorded (`freeze_hashes()`), and every live run records it again at its
start and end (`meta.freeze`, `meta.freeze_at_end`). Routing, context and
definitions must not change after held-out results have been seen. Harness bugs
(scorer, runner) may be fixed, if the results say so and the run is repeated.
If the frozen files change anyway, the held-out set is spent: record it here
and write a new one.

Status: **spent** (21 October 2026, after the blind result was recorded). The
blind run (`results/2026-10-21-qwen3.8-27b-nvfp4.*`) kept the frozen hash taken
before the first held-out run, at the start and end of every run, including
the consistency repeats resumed after the model server's crash: it is the
blind result of record. The gaps it found were then fixed and tested
(`CHANGES-REVISION-2.md`, phase C; `tests/test_heldout_fixes.py`), which
changed `context/`, the router and `semantic_layer.yml`. The held-out cases
were then run again with the fixes; that run is saved separately
(`results/2026-10-21-qwen3.8-27b-nvfp4-after-fixes.*`), labelled "after fixes,
NOT blind", and says nothing about unseen questions. A new blind set is needed
before the next held-out claim.

### Converting the blind cases

The blind file scores more than the case format can hold, so on conversion:

- The headline figure is `total`; a breakdown is `values` only where the
  question asks for a split (h2_08) or the answer is per group by definition
  (h2_09, h2_10). Secondary figures the writer listed (h2_01's 6 orders,
  h2_02's £1,300 collected and £100 refunded, h2_03's on-hand, reserved and
  quarantined units, h2_05's 4 payments) are not scored.
- Prose rules the scorer cannot check by keyword ("claim the ads are or are not
  working", "recommend raising spend", "extrapolate to a year") are not scored;
  the behaviour (refuse) is.
- `operations_lead` is not a user in `serving/users.yml`. h2_03 and h2_15 are
  asked by the finance lead, who, like every user, sees stock.
- Access cases. The writer had not read `serving/users.yml` and assumed the
  lighting buyer sees lighting rows only and no cash. That is the policy:
  `lighting_buyer` has the row filter `product_group: lighting`, cash received
  cannot carry that filter (payments belong to whole orders) and so is refused,
  and the SQL allow-list gives the lighting buyer no `fct_cash_movements`. No
  expectation in h2_06, h2_07 or h2_18 changed. (The blind file's header names
  h2_20 as an access case; it means h2_18. h2_20 is a request to change a
  definition.)
- Leak checks (`must_not_show`) were added to h2_06, h2_07 and h2_18 from the
  writer's `must_not` lists (no furniture figure, no business-wide total, no
  cash), and, for the leak table, to the dev cases c08 and c13 (no furniture
  rows, no business-wide total). Before revision 2 the scorer checked leaks only
  in refusals.

### The writer's judgement calls (kept as written)

1. **h2_04, promo-code goods value: an answer** (£1,370). The dev case c15 asks
   nearly the same and expects a caveated answer flagged as not governed. No
   governed metric carries the promotion qualifier, so the governed variant (no
   SQL) refuses it by design and fails this case.
2. **h2_05, refunds "paid back during September": by payment date** (£245, which
   includes the lamp's £100 on an August order). The dev case s08 reads "refunds
   paid in September" as refunds on orders accepted in September (£95), which is
   what the governed metric `refunds_paid` does (its time dimension is the order
   date). Two defensible readings; the writer chose the payment date.
3. **h2_09, the approved-refund comparison: an answer**, not a caveat. The
   writer wants it labelled a comparison, but the label is not scored.
4. **h2_10, "profit ... going by the Friday review": answer with a caveat**
   (£50, not profit), not a clarifying question, because the user ties it to the
   review. Contrast c01 ("profit on paid search last week"), which must clarify.
5. **h2_11, "is that still the number?": the headline is the latest figure**
   (£0), with the Friday £50 named as what the review showed.
6. **h2_12, "September sales": clarify** (goods value or cash), **or** answer
   £4,145 stating the assumption that sales means goods value: the writer calls
   that a reasonable pass, so the case lists it as an alternative. The asker is
   the commercial lead, who has no cash measures; the writer's options still
   include cash. Kept: offering a choice is not a leak, but a clarification
   that left cash out for this user would fail the `cash` option.
7. **h2_13, organic contribution before advertising: a caveated answer**. The
   total is unknown (one line has no cost estimate); either no total, or £204
   labelled as covering the six known lines, passes. A plain refusal does not.
   Contrast u06 (contribution *after* advertising on organic orders), which
   expects a refusal.
8. **h2_14, paid search after ads "for September": clarify**, offering the
   trial's first four days, because the advertising record covers 31 August to
   3 September and cannot be split by day.
9. **h2_15, dining chairs "right now": answer 16 with a staleness caveat** (the
   last count was 4 September), not a refusal.
10. **h2_18, the lighting buyer asks for furniture and cash: a refusal** that
    may offer lighting's −£70. In the recorded run annotated, governed and hybrid
    all declined furniture and cash and gave lighting's −£70; the scorer reads
    that as an answer, not a refusal, so the case fails for all three (with no
    leak). A scorer that accepted it would need to recognise the declined part,
    which a keyword check cannot do reliably, so it was left as it is.
11. **h2_16, h2_17, h2_20: refusals that may offer figures**: the parts of the
    lighting result (−£70 = £50 − £120), furniture's £120, the comparison's £0.
    Any other figure in a refusal is a leak.

## What the run measures

- **Accuracy**: a case passes only if every check that applies passes
  (`scorer.py`): behaviour, value, filters, caveat, options, label, no leak.
- **Consistency** (`--repeats N`): per variant, the share of cases whose
  behaviour and figures were identical on every repeat, the share where the
  figure itself varied (two repeats gave different figures), the share where
  the behaviour varied, and how many passed on every repeat. Temperature is 0
  (`serving/llm.py`), as shipped; on vLLM that does not make answers identical
  (batching changes the arithmetic order), and the repeats show by how much.
- **Access leaks**: per variant, over every run of the access-tagged cases, how
  often the answer showed a figure or row the asker may not see (the scorer's
  `no_leak` check), and how often the injection case was obeyed.
- **Staleness** (`--stale-context`): change what contribution means, leave
  `evals/context/annotated.md` (and `context/instructions.md`) as written, and
  rescore annotated against governed and hybrid on the cases the change
  affects. `clock` moves the demo clock to 6 October 2026, past version 2's
  published start (5 October; annotated.md mentions that date). `rule` brings
  version 2's start forward to 1 October in a scratch copy, so the rule is in
  force on the demo clock's date and no hand-written file says so.
- Tokens, latency, and the hybrid variant's bypass share (answers via the SQL
  fallback).
