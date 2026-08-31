import pytest
from saitenka_dict.metadata import FrequencyValue, parse_frequency


def test_frequency_forms_share_one_normalization_contract():
    assert parse_frequency(3) == FrequencyValue(None, 3, None)
    assert parse_frequency("twenty-four (24)") == FrequencyValue(None, 24, "twenty-four (24)")
    # A leading number is the whole of what the string says; the pill shows the parsed rank, not the
    # raw text. A trailing "(24)" or a wordy string keeps its text — that text carries something.
    assert parse_frequency("123 occurrences") == FrequencyValue(None, 123, None)
    assert parse_frequency("118,121") == FrequencyValue(None, 118, None)
    assert parse_frequency("N5") == FrequencyValue(None, None, "N5")
    assert parse_frequency(
        {"reading": "うちこむ", "frequency": {"value": 30, "displayValue": "thirty"}}
    ) == FrequencyValue("うちこむ", 30, "thirty")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8912, 8912),
        (8912.0, 8912.0),
        ("8912", 8912),
        # A grouped "rank, occurrences" display packs both numbers into one string; the rank is the
        # first, so the leading integer wins and the comma is never stripped into 118121.
        ("118,121", 118),
        ("118, 121", 118),
        ("  73 ", 73),
        # No number at all → no rank. A synthetic 0 would sort as the most frequent word there is.
        ("N5", None),
        ("eighteen", None),
        (True, None),  # bool is not rank 1
        (None, None),
    ],
)
def test_a_value_without_a_number_carries_no_rank(value, expected):
    assert parse_frequency(value).value == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (8912, (None, 8912, None)),
        ({"reading": "ほんめい", "frequency": 8912}, ("ほんめい", 8912, None)),
        ({"value": 4073, "displayValue": "4073㋕"}, (None, 4073, "4073㋕")),
        ({"value": "118,121"}, (None, 118, None)),
        # The JLPT dictionary rides the freq mode with a -1 sentinel; the level is in displayValue.
        ({"frequency": {"value": -1, "displayValue": "N5"}}, (None, -1, "N5")),
    ],
)
def test_every_wild_frequency_shape_normalizes(data, expected):
    parsed = parse_frequency(data)
    assert (parsed.reading, parsed.value, parsed.display) == expected
