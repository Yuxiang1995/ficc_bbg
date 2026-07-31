import unittest

from .score_eval import normalize_value, score_dataset


def _sample(
    sample_id,
    prediction,
    gold,
    availability,
    status="completed",
    difficulty_gold="easy",
):
    return {
        "sample_id": sample_id,
        "selection_bucket": "easy",
        "difficulty_candidate": "easy",
        "input": {"quote_line": "TEST 5.0 01/30 100/101"},
        "prediction_snapshot": [prediction] if prediction is not None else [],
        "annotation": {
            "status": status,
            "difficulty_gold": difficulty_gold,
            "field_availability": availability,
            "gold": gold,
            "annotator": "test",
            "remark": None,
        },
    }


class ScoreEvalTest(unittest.TestCase):
    def test_normalization(self) -> None:
        self.assertEqual(normalize_value("ticker", " abcd "), "ABCD")
        self.assertEqual(normalize_value("coupon_type", "floating rate"), "FRN")
        self.assertEqual(normalize_value("maturity_date", "09/29"), "2029-09")
        self.assertEqual(normalize_value("maturity_date", "1/2/30"), "2030-01-02")

    def test_scores_correct_omission_and_hallucination(self) -> None:
        fields = {
            "ticker": "present",
            "currency": "absent",
            "maturity_date": "inferable",
            "coupon_type": "inferable",
            "coupon_rate": "present",
        }
        gold = {
            "ticker": "TEST",
            "currency": None,
            "maturity_date": "2030-01",
            "coupon_type": "Fixed",
            "coupon_rate": 5.0,
        }
        prediction = {
            "ticker": "test",
            "currency": "USD",
            "maturity_date": "01/30",
            "coupon_type": "fixed",
            "coupon_rate": 5.0,
        }
        report = score_dataset(
            {"schema_version": "1.0", "dataset_name": "test", "samples": [
                _sample("eval_0001", prediction, gold, fields)
            ]}
        )
        overall = report["metrics"]["overall"]
        self.assertEqual(overall["evaluated_fields"], 5)
        self.assertEqual(overall["correct_fields"], 4)
        self.assertAlmostEqual(overall["micro_field_accuracy"], 4 / 5)
        self.assertAlmostEqual(overall["answerable_field_accuracy"], 1.0)
        self.assertEqual(overall["absent_field_null_accuracy"], 0.0)
        self.assertEqual(report["error_summary"], {
            "hallucination": 1,
        })

    def test_pending_samples_produce_null_metrics(self) -> None:
        report = score_dataset(
            {
                "samples": [
                    _sample(
                        "eval_0001",
                        {},
                        {},
                        {},
                        status="pending",
                    )
                ]
            }
        )
        self.assertEqual(report["annotation_coverage"]["evaluated_samples"], 0)
        self.assertIsNone(
            report["metrics"]["overall"]["micro_field_accuracy"]
        )

    def test_coupon_rate_tolerance(self) -> None:
        availability = {
            field: "ambiguous"
            for field in (
                "ticker",
                "currency",
                "maturity_date",
                "coupon_type",
                "coupon_rate",
            )
        }
        availability["coupon_rate"] = "present"
        gold = {"coupon_rate": 5.0}
        prediction = {"coupon_rate": 5.0000004}
        report = score_dataset(
            {"samples": [_sample(
                "eval_0001",
                prediction,
                gold,
                availability,
            )]}
        )
        self.assertEqual(
            report["metrics"]["per_field"]["coupon_rate"]["correct"],
            1,
        )

    def test_empty_prediction_is_scored_as_null(self) -> None:
        availability = {
            field: "absent"
            for field in (
                "ticker",
                "currency",
                "maturity_date",
                "coupon_type",
                "coupon_rate",
            )
        }
        gold = {field: None for field in availability}
        report = score_dataset(
            {"samples": [_sample(
                "eval_0001",
                None,
                gold,
                availability,
            )]}
        )
        self.assertEqual(
            report["metrics"]["overall"][
                "all_critical_fields_exact_accuracy"
            ],
            1.0,
        )

    def test_strict_annotations_reject_invalid_completed_sample(self) -> None:
        availability = {
            field: "pending"
            for field in (
                "ticker",
                "currency",
                "maturity_date",
                "coupon_type",
                "coupon_rate",
            )
        }
        with self.assertRaises(ValueError):
            score_dataset(
                {"samples": [_sample(
                    "eval_0001",
                    {},
                    {},
                    availability,
                )]},
                strict_annotations=True,
            )


if __name__ == "__main__":
    unittest.main()
