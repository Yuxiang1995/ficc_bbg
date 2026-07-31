import unittest

from .build_eval_set import (
    _select_diverse,
    _select_special,
    classify_missing_fields,
    difficulty_bucket,
    paragraph_head,
)


class EvalDatasetBuilderTest(unittest.TestCase):
    def test_complete_prediction_is_easy(self) -> None:
        prediction = {
            "ticker": "IIFOIN",
            "currency": "USD",
            "maturity_date": "2029-09-30",
            "coupon_type": "Fixed",
            "coupon_rate": 7.6,
        }
        self.assertEqual(classify_missing_fields(prediction), [])
        self.assertEqual(difficulty_bucket(0), "easy")

    def test_partial_maturity_counts_as_present(self) -> None:
        prediction = {
            "ticker": "IIFOIN",
            "currency": "USD",
            "maturity_date": None,
            "maturity_text": "2029-09",
            "coupon_type": "Fixed",
            "coupon_rate": 7.6,
        }
        self.assertNotIn("maturity_date", classify_missing_fields(prediction))

    def test_missing_fields_are_bucketed(self) -> None:
        prediction = {
            "ticker": "IIFOIN",
            "issuer": None,
            "currency": None,
            "maturity_date": "2029-09",
            "coupon_type": "Fixed",
            "coupon_rate": 7.6,
        }
        missing = classify_missing_fields(prediction)
        self.assertEqual(missing, ["currency"])
        self.assertEqual(difficulty_bucket(len(missing)), "missing_1")
        self.assertEqual(difficulty_bucket(4), "missing_4_plus")

    def test_missing_issuer_is_not_a_critical_field(self) -> None:
        prediction = {
            "ticker": "IIFOIN",
            "issuer": None,
            "currency": "USD",
            "maturity_date": "2029-09",
            "coupon_type": "Fixed",
            "coupon_rate": 7.6,
        }
        self.assertEqual(classify_missing_fields(prediction), [])

    def test_paragraph_context_uses_first_five_lines(self) -> None:
        text = "\n".join("line {}".format(index) for index in range(1, 8))
        self.assertEqual(
            paragraph_head(text),
            "\n".join("line {}".format(index) for index in range(1, 6)),
        )

    def test_sampling_is_deterministic_and_unique(self) -> None:
        candidates = [
            {
                "source_fingerprint": "fingerprint_{}".format(index),
                "ticker_for_diversity": "T{}".format(index % 3),
                "quote_source": "source_{}".format(index % 2),
            }
            for index in range(20)
        ]
        first = _select_diverse(candidates, 8, 20260728, "test", set())
        second = _select_diverse(candidates, 8, 20260728, "test", set())
        first_ids = [item["source_fingerprint"] for item in first]
        second_ids = [item["source_fingerprint"] for item in second]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(set(first_ids)), 8)

    def test_special_sampling_covers_multiple_reasons(self) -> None:
        candidates = []
        for reason in (
            "message_type_unknown",
            "message_type_quote_list",
            "partial_maturity",
            "one_sided_quote",
        ):
            for index in range(4):
                candidates.append(
                    {
                        "source_fingerprint": "{}_{}".format(reason, index),
                        "ticker_for_diversity": "T{}".format(index),
                        "quote_source": "source",
                        "special_format_reasons": [reason],
                    }
                )
        selected = _select_special(candidates, 8, 20260728, set())
        selected_reasons = {
            item["special_format_reasons"][0] for item in selected
        }
        self.assertEqual(len(selected), 8)
        self.assertEqual(
            selected_reasons,
            {
                "message_type_unknown",
                "message_type_quote_list",
                "partial_maturity",
                "one_sided_quote",
            },
        )


if __name__ == "__main__":
    unittest.main()
