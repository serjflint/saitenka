from saitenka_dict.metadata import FrequencyValue, parse_frequency


def test_frequency_forms_share_one_normalization_contract():
    assert parse_frequency(3) == FrequencyValue(None, 3, None)
    assert parse_frequency("eighteen") == FrequencyValue(None, 0, "eighteen")
    assert parse_frequency("twenty-four (24)") == FrequencyValue(None, 24, "twenty-four (24)")
    assert parse_frequency("123 occurrences") == FrequencyValue(None, 123, "123 occurrences")
    assert parse_frequency(
        {"reading": "うちこむ", "frequency": {"value": 30, "displayValue": "thirty"}}
    ) == FrequencyValue("うちこむ", 30, "thirty")
