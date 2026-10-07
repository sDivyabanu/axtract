"""AXTRACT Verify: text and number normalisation.

The rule under test: fold formatting, never fold meaning. Every "equal" assertion has a
matching "must stay different" assertion.
"""

from __future__ import annotations

import pytest

from verify.normalize import (
    canonicalize_numbers,
    hyphen_variants,
    normalize_text,
    numbers_equivalent,
    parse_number,
)


def canon(s: str, **kw) -> str | None:
    p = parse_number(s, **kw)
    return p.canonical() if p else None


class TestTextWhitespaceAndLines:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  a   b\t\tc  ", "a b c"),
            ("a\nb", "a b"),
            ("a\r\nb\rc", "a b c"),
            ("a b c", "a b c"),
            ("a b c d　e", "a b c d e"),
            ("zero​width", "zerowidth"),
            ("﻿bom", "bom"),
            ("", ""),
        ],
    )
    def test_whitespace_forms_fold_to_single_spaces(self, raw, expected):
        assert normalize_text(raw) == expected

    def test_preserve_lines_keeps_line_structure_but_tidies_each_line(self):
        assert normalize_text("  a  b \n\n  c\r\nd  ", preserve_lines=True) == "a b\nc\nd"

    def test_control_characters_are_dropped_but_tab_and_newline_survive_as_whitespace(self):
        assert normalize_text("a\x00b\x07c\td") == "abc d"

    def test_none_like_input_is_safe(self):
        assert normalize_text("") == "" and normalize_text("   ") == ""


class TestLigaturesAndUnicode:
    @pytest.mark.parametrize(
        "raw,expected",
        [("ﬁnancial", "financial"), ("oﬃce", "office"), ("ﬂow", "flow"), ("ﬀ", "ff"), ("ﬄ", "ffl"), ("ﬆ", "st")],
    )
    def test_ligatures_expand(self, raw, expected):
        assert normalize_text(raw) == expected

    def test_ligature_folding_can_be_switched_off(self):
        assert normalize_text("ﬁ", fold_ligatures=False) == "ﬁ"

    def test_composed_and_decomposed_accents_compare_equal(self):
        assert normalize_text("café") == normalize_text("café")

    @pytest.mark.parametrize(
        "a,b",
        [("m²", "m2"), ("x₁", "x1"), ("½", "1/2"), ("ℓ", "l"), ("Ⅳ", "IV"), ("™", "TM"), ("ＡＢＣ", "ABC")],
    )
    def test_compatibility_forms_are_NOT_folded_because_they_carry_meaning(self, a, b):
        assert normalize_text(a) != normalize_text(b)

    def test_zero_width_joiner_is_preserved(self):
        assert normalize_text("a‍b") == "a‍b"

    def test_quotes_fold_by_default_and_can_be_kept(self):
        assert normalize_text("“it’s”") == '"it\'s"'
        assert normalize_text("“it’s”", fold_quotes=False) == "“it’s”"

    def test_dashes_are_kept_unless_asked(self):
        assert normalize_text("2019–2020") == "2019–2020"
        assert normalize_text("2019–2020", fold_dashes=True) == "2019-2020"


class TestCase:
    def test_case_sensitive_by_default(self):
        assert normalize_text("Revenue") != normalize_text("revenue")

    def test_case_insensitive_mode(self):
        assert normalize_text("Revenue", case_sensitive=False) == normalize_text("REVENUE", case_sensitive=False)

    def test_casefold_handles_sharp_s(self):
        assert normalize_text("STRASSE", case_sensitive=False) == normalize_text("straße", case_sensitive=False)

    def test_case_insensitivity_does_not_merge_different_words(self):
        assert normalize_text("Total", case_sensitive=False) != normalize_text("Totals", case_sensitive=False)


class TestHyphenation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("inter-\nnational", "international"),
            ("inter-\n  national", "international"),
            ("inter-  \r\nnational", "international"),
            ("Tel-\nAviv", "Tel-Aviv"),  # next word capitalised: a real hyphen
            ("COVID-\n19", "COVID-19"),  # digit follows: a real hyphen
            ("well-known", "well-known"),  # no line break: untouched
            ("pre-\nexisting condition", "preexisting condition"),
            ("a - \nb", "a - b"),  # spaced dash is not a word hyphen
            ("end-\n", "end-"),
        ],
    )
    def test_line_end_hyphens(self, raw, expected):
        assert normalize_text(raw) == expected

    def test_soft_hyphen_inside_a_word_disappears(self):
        assert normalize_text("inter­national") == "international"

    def test_soft_hyphen_at_a_line_break_joins_the_word(self):
        assert normalize_text("inter­\nnational") == "international"

    def test_dehyphenation_can_be_disabled_and_keeps_the_hyphen(self):
        assert normalize_text("inter-\nnational", dehyphenate=False) == "inter-national"

    def test_ambiguous_case_offers_both_readings(self):
        joined, kept = hyphen_variants("e-\nmail")
        assert (joined, kept) == ("email", "e-mail")

    def test_unambiguous_text_has_one_reading(self):
        joined, kept = hyphen_variants("plain text")
        assert joined == kept == "plain text"


class TestNumberParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1234", "1234"),
            ("1,234", "1234"),
            ("1,234,567", "1234567"),
            ("1,234.50", "1234.50"),
            ("1.234,50", "1234.50"),
            ("1 234,50", "1234.50"),
            ("1 234,50", "1234.50"),
            ("1 234 567", "1234567"),
            ("1'234'567.5", "1234567.5"),
            ("12,34,567", "1234567"),
            (".5", "0.5"),
            ("0.5", "0.5"),
            ("١٢٣", "123"),
            ("０１２", "012"),
        ],
    )
    def test_separators_and_digits(self, raw, expected):
        assert canon(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "abc", "1e5", "NaN", "Infinity", "1,2,3", "1,23,45", "12.34.56", "1..2", "--5", "5-5", "2019-2020",
         "$5%", "$USD 5", "USD $5", "5 5", "1,", ",", "(5", "5)"],
    )
    def test_things_that_are_not_one_clean_number_return_none(self, raw):
        assert parse_number(raw) is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("-5", "-5"), ("+5", "5"), ("−5", "-5"), ("(5)", "-5"), ("(1,234.50)", "-1234.50"),
            ("5-", "-5"), ("-$5", "-$5"), ("$-5", "-$5"), ("($5)", "-$5"), ("- 5", "-5"),
        ],
    )
    def test_negative_spellings_agree(self, raw, expected):
        assert canon(raw) == expected

    def test_double_negatives_are_rejected(self):
        assert parse_number("-(5)") is None and parse_number("(-5)") is None and parse_number("-5-") is None

    def test_identifier_like_numbers_keep_their_digits_in_canonical_form(self):
        assert canon("007") == "007" and canon("00123") == "00123" and canon("00.5") == "00.5"
        assert canon("007") != canon("7")

    def test_negative_zero_is_just_zero(self):
        assert canon("-0") == "0" and canon("(0.00)") == "0.00"

    @pytest.mark.parametrize(
        "raw,expected",
        [("12.5%", "12.5%"), ("12.5 %", "12.5%"), ("12.5 percent", "12.5%"), ("12,5 %", "12.5%"), ("％12", None),
         ("-3%", "-3%"), ("(3%)", "-3%")],
    )
    def test_percent_spellings(self, raw, expected):
        assert canon(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("$18.2M", "$18200000"), ("$18.2 million", "$18200000"), ("$18,200,000", "$18200000"),
            ("$18.20M", "$18200000"), ("18.2M", "18200000"), ("5k", "5000"), ("5K", "5000"), ("$5.5bn", "$5500000000"),
            ("$2 trillion", "$2000000000000"), ("3 MM", "3000000"), ("$5m", "$5000000"),
        ],
    )
    def test_scale_suffixes_and_words(self, raw, expected):
        assert canon(raw) == expected

    @pytest.mark.parametrize("raw", ["5m", "5mm", "5b", "5t", "5 tn", "5 apples", "5 xx"])
    def test_ambiguous_lowercase_suffixes_are_not_read_as_scales(self, raw):
        assert parse_number(raw) is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("$5", "$5"), ("5 USD", "USD5"), ("USD 5", "USD5"), ("€5", "€5"), ("5 EUR", "€5"),
            ("EUR 5", "€5"), ("5 €", "€5"), ("£5", "£5"), ("GBP 5", "£5"), ("₹5", "₹5"),
        ],
    )
    def test_currency_position_and_aliases(self, raw, expected):
        assert canon(raw) == expected

    def test_dollar_is_not_merged_with_usd_because_dollar_is_ambiguous(self):
        assert canon("$5") != canon("USD 5")
        assert canon("¥5") != canon("JPY 5")

    def test_currency_on_both_sides_is_rejected(self):
        assert parse_number("$5 USD") is None


class TestLocaleAndAmbiguity:
    def test_unambiguous_mixed_separators_agree_across_locales(self):
        assert canon("1.234,56") == canon("1,234.56") == "1234.56"

    @pytest.mark.parametrize("raw", ["1,234", "1.234"])
    def test_single_group_of_three_digits_is_flagged_ambiguous(self, raw):
        assert parse_number(raw).ambiguous is True

    @pytest.mark.parametrize("raw", ["12,5", "1,2345", "1234.5", "1,234,567", "1.234,5"])
    def test_these_are_not_ambiguous(self, raw):
        assert parse_number(raw).ambiguous is False

    def test_auto_locale_reads_1_234_as_thousands_and_1_dot_234_as_a_decimal(self):
        assert canon("1,234") == "1234" and canon("1.234") == "1.234"

    def test_explicit_locales_override_the_guess(self):
        assert canon("1.234", locale="eu") == "1234"
        assert canon("1,234", locale="eu") == "1.234"
        assert canon("1,234", locale="en") == "1234"

    def test_the_two_readings_of_an_ambiguous_number_are_not_equivalent(self):
        assert not numbers_equivalent("1,234", "1.234")


class TestNumberEquivalence:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("$18.2M", "$18.2 million"), ("$18.2M", "$18,200,000"), ("(1,234.50)", "-1,234.50"),
            ("1,234.50-", "−1234.50"), ("12.5%", "12.5 percent"), ("1.234,56", "1,234.56"),
            ("€5", "5 EUR"), (".5", "0.5"), ("1 234,56", "1 234,56"), ("+7", "7"),
        ],
    )
    def test_same_quantity_different_formatting(self, a, b):
        assert numbers_equivalent(a, b)

    @pytest.mark.parametrize(
        "a,b",
        [
            ("$18.2M", "$13.2M"),  # the headline case: one digit changes the meaning
            ("$18.2M", "$18.2K"), ("$18.2M", "$182M"), ("$18.2M", "$1.82M"),
            ("1,234.50", "-1,234.50"), ("12.5%", "12.5"), ("12.5%", "0.125"), ("$5", "5"), ("$5", "€5"),
            ("$5", "USD 5"), ("007", "7"), ("00123", "123"), ("5", "50"), ("5", "5.1"), ("1,234", "1.234"),
            ("100", "10,0"), ("£5", "$5"), ("5%", "5 USD"),
        ],
    )
    def test_different_quantities_stay_different(self, a, b):
        assert not numbers_equivalent(a, b)

    def test_trailing_zeros_matter_by_default_and_can_be_relaxed(self):
        assert not numbers_equivalent("1.50", "1.5")
        assert numbers_equivalent("1.50", "1.5", precision="value")
        assert not numbers_equivalent("1", "1.0")

    def test_precision_does_not_matter_once_a_scale_suffix_is_used(self):
        assert numbers_equivalent("$18.20M", "$18.2M")

    def test_identifier_like_numbers_must_match_digit_for_digit(self):
        assert numbers_equivalent("007", "007")
        assert not numbers_equivalent("007", "7", precision="value")
        assert parse_number("0.5").identifier_like is False
        assert parse_number("007").identifier_like is True
        assert parse_number("1,007").identifier_like is False

    def test_non_numbers_are_never_equivalent(self):
        assert not numbers_equivalent("abc", "abc")
        assert not numbers_equivalent("5", "five")


class TestCanonicalNumbersInText:
    def test_headline_example(self):
        a = "Revenue was $18.2 million in 2024."
        b = "Revenue was $18,200,000 in 2024."
        c = "Revenue was $13.2 million in 2024."
        na, nb, nc = (normalize_text(x, canonical_numbers=True) for x in (a, b, c))
        assert na == nb == "Revenue was $18200000 in 2024."
        assert na != nc

    def test_numbers_inside_prose(self):
        t = "Up 12.5 % to 1,234.50 from (3.5) with 5 000 items in 2019-2020 and code 007."
        out = canonicalize_numbers(t)
        assert "12.5%" in out and "1234.50" in out and "2019-2020" in out and "code 007." in out

    def test_adjacent_numbers_are_not_merged(self):
        assert canonicalize_numbers("3.14.15, 1,50") == "3.14.15, 1.50"
        assert canonicalize_numbers("values 5 6 7") == "values 5 6 7"

    def test_dates_and_ranges_are_left_alone(self):
        for t in ("12/31/2020", "2019-2020", "10:30", "v1.2.3", "ISBN 978-3-16-148410-0"):
            assert canonicalize_numbers(t) == t

    def test_words_after_numbers_are_not_swallowed_as_scales(self):
        assert canonicalize_numbers("5 of 6 in 2 days") == "5 of 6 in 2 days"
        assert canonicalize_numbers("about 5m long") == "about 5m long"

    def test_prose_parentheses_are_not_negative_numbers(self):
        assert canonicalize_numbers("see note (5) below") == "see note (5) below"

    def test_nbsp_grouped_numbers_are_recognised_before_spaces_fold(self):
        assert normalize_text("Total 1 234,56 €", canonical_numbers=True) == "Total €1234.56"

    def test_identifier_like_tokens_are_untouched(self):
        assert canonicalize_numbers("order 00123 shipped") == "order 00123 shipped"

    @pytest.mark.parametrize("a,b", [("7 apples", "70 apples"), ("$5 each", "5 each"), ("5% off", "5 off"),
                                     ("12,5 %", "125 %"), ("$18.2M", "$18.2K")])
    def test_texts_with_different_numbers_stay_different_after_canonicalisation(self, a, b):
        assert normalize_text(a, canonical_numbers=True) != normalize_text(b, canonical_numbers=True)


class TestNormalizerProperties:
    SAMPLES = [
        "Plain text.", "  messy   spacing\r\nand breaks ", "inter-\nnational ﬁnancial oﬃce",
        "$18.2 million vs $18,200,000 and 12.5 %", "(1,234.50) and −5 and 5-", "Tel-\nAviv COVID-\n19 e-\nmail",
        "“quoted” text with ‘single’", "x² + y₂ = ½", "café café", "﻿​zero­width",
        "1 234,56 €; 12 345,67 EUR", "007 and 0.50 and .5",
    ]

    @pytest.mark.parametrize("text", SAMPLES)
    @pytest.mark.parametrize("canon_nums", [False, True])
    def test_idempotent(self, text, canon_nums):
        once = normalize_text(text, canonical_numbers=canon_nums)
        assert normalize_text(once, canonical_numbers=canon_nums) == once

    @pytest.mark.parametrize("text", SAMPLES)
    def test_case_insensitive_mode_is_idempotent_too(self, text):
        once = normalize_text(text, case_sensitive=False)
        assert normalize_text(once, case_sensitive=False) == once

    @pytest.mark.parametrize("text", SAMPLES)
    def test_output_has_no_leading_trailing_or_double_spaces(self, text):
        out = normalize_text(text)
        assert out == out.strip() and "  " not in out

    def test_digits_are_never_changed_by_plain_text_normalisation(self):
        text = "Q3 2024: 18.2 vs 13.2 (and 1,234)"
        assert [c for c in normalize_text(text) if c.isdigit()] == [c for c in text if c.isdigit()]
