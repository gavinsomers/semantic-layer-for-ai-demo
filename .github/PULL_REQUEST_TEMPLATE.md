## What changes

<!-- One or two sentences. Name every metric touched. -->

## Kind of change

<!-- `uv run python -m sl diff` prints this for you. Tick all that apply. -->

- [ ] Added metric
- [ ] Metadata only (label, description, owner, status, deprecation)
- [ ] Formula, filter or grain of an existing metric (BREAKING: ship as a new version instead)
- [ ] Removed or renamed metric (BREAKING)
- [ ] Context only (synonyms, verified queries, instructions, ambiguity rules)

## Impact

<!-- Paste the impact lines from `sl diff`: affected verified queries, eval cases, consumers. -->

## Checks

- [ ] `uv run python -m sl ci` passes locally
- [ ] Verified queries re-run; any expected value that moved is explained below
- [ ] Eval smoke set passes; full set re-run if a metric's meaning changed
- [ ] For a new version: the old version is marked `status: deprecated` with a sunset date and a notice
- [ ] Reviewed files re-locked (`sl lock --reviewer ...`) by a reviewer, not by the author

## Business sign-off

<!-- Required for any change to what a number means. Code owners alone are not enough. -->

| Metric | Owner | Approver | Decision | Effective from | Date |
|---|---|---|---|---|---|
| | | | approve / reject | | |

- [ ] The metric owner has read the change in plain words, not only the code
- [ ] Consumers listed in the impact section have been told (changelog entry or notice)
