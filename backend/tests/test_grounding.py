import pytest

from sarthi.llm.grounding import grounded

FACTS = {"prob": 0.84, "qty": 1482, "par": 18500, "name": "Lays Classic 26g"}


@pytest.mark.parametrize("sentence", [
    "84% stockout risk on Lays Classic 26g; order 1,482 units to protect ₹18.5K",
    "Order 1482 units of Lays Classic 26g.",
    "Stockout probability is 0.84, so ₹18,500 of profit is exposed.",
    "About 18.5 thousand rupees is at risk on Lays Classic 26g.",
    "Lays Classic 26g needs a reorder.",                      # no numbers at all
    "Two suppliers were compared; 2 were feasible.",          # a bare small count is allowed
])
def test_sentences_using_only_known_numbers_pass(sentence):
    assert grounded(sentence, FACTS)


@pytest.mark.parametrize("sentence", [
    "Order 1,500 units of Lays Classic 26g.",                 # invented quantity
    "92% stockout risk on Lays Classic 26g.",                 # invented probability
    "Protect ₹20K by ordering 1,482 units.",                  # invented value
    "Stock will last 3 days.",                                # small number with a unit must be a fact
    "Risk is 2%.",
    "Costs ₹4 per unit.",
    "Lays Classic 30g is at risk.",                           # altered product name exposes a new number
    "",
    "   ",
])
def test_sentences_with_unknown_numbers_fail(sentence):
    assert not grounded(sentence, FACTS)


def test_rounding_and_scaling_variants():
    facts = {"doc": 1.578, "lead": 12, "par": 247300, "share": 0.125}
    assert grounded("1.6 days of cover against a 12-day lead time", facts)
    assert grounded("2 days of cover", facts)                 # rounded to a whole number
    assert grounded("₹247.3K at risk, about ₹2.47 lakh", facts)
    assert grounded("12.5% of capital", facts)
    assert not grounded("1.9 days of cover", facts)
    assert grounded("₹250K at risk", facts)                   # 1.09 % off: inside the 1.1 % rounding tolerance
    assert not grounded("₹255K at risk", facts)               # 3.1 % off: a different number


def test_nested_facts_ids_and_numeric_strings():
    facts = {"sku": {"id": "SKU003", "name": "Amul Butter 500g"}, "po": "PO-2A7F", "options": [{"days": 7}, {"days": 3}],
             "price": "11.60", "ok": True}
    assert grounded("PO-2A7F for SKU003 (Amul Butter 500g) at ₹11.6, arriving in 3 or 7 days", facts)
    assert not grounded("PO-2A7F arriving in 5 days", facts)
    assert not grounded("Amul Butter 500g: order 500 units", facts)   # the 500 in the name does not license a quantity


def test_ranges_dates_and_length_limit():
    facts = {"low": 3, "high": 5}
    assert grounded("between 3-5 days", facts)                # the hyphen is not a minus sign
    assert not grounded("due on 2026-10-08", facts)           # dates are numbers too, and are not in the facts
    assert not grounded("3 days. " * 200, facts)              # over the length limit
