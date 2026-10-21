"""The model may reword an answer, never change its figures (guide ch 4)."""

from serving import contract
from serving.agent import finish, stray_numbers


def answer(total=50, values=({"product_group": "furniture", "contribution": 120},
                             {"product_group": "lighting", "contribution": -70})):
    return contract.new("What did we make?", "finance_lead", "supported", "governed",
                        total=total, values=list(values), metrics=["contribution_after_ads"],
                        cutoff={"id": "friday_review", "at": "2026-09-04 06:00:00"},
                        time_window={"start": "2026-08-31", "end": "2026-09-03"},
                        compiled_sql="select 1",
                        lineage=[{"metric": "contribution_after_ads", "version": 1,
                                  "owner": "finance lead", "approver": "head of data",
                                  "status": "certified", "last_reviewed": "2026-10-02"}])


def test_formats_of_the_same_figure_all_pass():
    for text in ("We made £50.", "We made £50.00.", "We made 50 GBP.", "We made 50 pounds.",
                 "Furniture £120, lighting −£70, total £50.", "Furniture made £120.0."):
        assert stray_numbers(text, answer()) == set(), text


def test_a_changed_or_unlabelled_figure_is_stray():
    assert stray_numbers("We made £60.", answer()) == {60.0}
    assert stray_numbers("We made 60.", answer()) == {60.0}          # no currency sign needed
    assert stray_numbers("Furniture minus lighting is £190.", answer()) == {190.0}  # arithmetic


def test_dates_and_cutoff_in_the_contract_may_be_quoted():
    text = "£50 for orders accepted 31 August to 3 September 2026, recorded by 06:00 on 4 September."
    assert stray_numbers(text, answer()) == set()


def test_a_fraction_may_be_quoted_as_a_percentage():
    rate = answer(total=0.0229, values=())
    assert stray_numbers("The refund rate was 2.29%.", rate) == set()
    # 3% is stray as a fraction (0.03); the bare 3 is allowed only because the
    # contract's dates contain a 3. The check narrows what can slip through; it
    # does not close it, which is why the contract, not the sentence, is the answer.
    assert 0.03 in stray_numbers("The refund rate was 3%.", rate)


def test_stray_wording_is_withheld_and_the_contract_kept():
    result = finish("What did we make?", "finance_lead", "We made £60.", [answer()])
    assert result["total"] == 50 and "£60" not in result["answer_text"]
    assert any("withheld" in c for c in result["caveats"])


# --- Signs: a loss quoted as a gain is withheld -----------------------------------

def test_a_negative_figure_given_as_positive_is_withheld():
    from serving.agent import unsigned_negatives
    assert unsigned_negatives("Lighting made £70.", answer()) == {70.0}
    result = finish("What did we make?", "finance_lead", "Furniture made £120 and lighting made £70.",
                    [answer()])
    assert "lighting made £70" not in result["answer_text"]
    assert any("without saying it is negative (70)" in c for c in result["caveats"])


def test_a_negative_figure_marked_negative_passes():
    from serving.agent import unsigned_negatives
    for text in ("Lighting −£70.", "Lighting -£70.", "Lighting was -70.", "Lighting lost £70.",
                 "Lighting made a loss of £70.", "Lighting was down £70 after advertising.",
                 "Lighting: £70 below zero.", "Lighting was negative at £70.",
                 "Furniture £120, lighting −£70, total £50."):
        assert unsigned_negatives(text, answer()) == set(), text


def test_the_marker_must_be_near_the_number_in_the_same_sentence():
    from serving.agent import unsigned_negatives
    assert unsigned_negatives("Lighting lost money. Lighting made £70.", answer()) == {70.0}
    far = "Lighting was a loss in August, as everyone expected, and this week it made £70."
    assert unsigned_negatives(far, answer()) == {70.0}


# --- A model that declines may not carry a figure ---------------------------------

def test_a_declining_answer_returns_the_refusal_not_the_figure():
    """Eval case c15: the words said no governed figure; the contract carried £4,145."""
    goods = answer(total=4145, values=())
    text = "I can't give a governed figure for orders that used a promotion."
    result = finish("What was the goods value of orders that used a promotion?", "finance_lead",
                    text, [goods])
    assert result["path"] == "refused" and result["total"] is None and result["values"] == []
    assert "4,145" not in result["answer_text"] and "4145" not in result["answer_text"]


def test_a_caveat_that_says_cannot_is_not_a_decline_when_the_figure_is_given():
    text = "We made £50. Advertising cannot be split below product group."
    result = finish("What did we make?", "finance_lead", text, [answer()])
    assert result["path"] == "governed" and result["answer_text"] == text
