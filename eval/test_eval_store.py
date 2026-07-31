import json
import tempfile
import unittest
from pathlib import Path

from ficc_bbg_parser.eval_store import EvaluationDatasetStore


def _annotation(status="pending"):
    return {
        "status": status,
        "difficulty_gold": "easy" if status == "completed" else None,
        "field_availability": {
            "ticker": "present" if status == "completed" else "pending",
            "currency": "inferable" if status == "completed" else "pending",
            "maturity_date": "present" if status == "completed" else "pending",
            "coupon_type": "inferable" if status == "completed" else "pending",
            "coupon_rate": "present" if status == "completed" else "pending",
        },
        "gold": {
            "ticker": "TEST" if status == "completed" else None,
            "currency": "USD" if status == "completed" else None,
            "maturity_date": "2030-01" if status == "completed" else None,
            "coupon_type": "Fixed" if status == "completed" else None,
            "coupon_rate": 5.0 if status == "completed" else None,
        },
        "annotator": "tester",
        "remark": None,
    }


class EvaluationDatasetStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.input_path = root / "eval_dataset.json"
        self.output_path = root / "eval_annotated.json"
        self.dataset = {
            "schema_version": "1.0",
            "metadata": {"seed": 1},
            "samples": [
                {
                    "sample_id": "eval_0001",
                    "input": {"quote_line": "TEST 5.0 01/30 100/101"},
                    "annotation": _annotation(),
                },
                {
                    "sample_id": "eval_0002",
                    "input": {"quote_line": "TEST2 4.0 01/31 99/100"},
                    "annotation": _annotation(),
                },
            ],
        }
        self.input_path.write_text(
            json.dumps(self.dataset),
            encoding="utf-8",
        )
        self.store = EvaluationDatasetStore(
            self.input_path,
            self.output_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_creates_working_copy_without_changing_source(self):
        source_before = self.input_path.read_text(encoding="utf-8")

        result = self.store.save_annotation(
            "eval_0001",
            _annotation(status="completed"),
        )

        self.assertTrue(self.output_path.is_file())
        self.assertEqual(
            self.input_path.read_text(encoding="utf-8"),
            source_before,
        )
        self.assertEqual(result["progress"]["completed"], 1)
        loaded = self.store.load()
        self.assertEqual(loaded["loaded_from"], "working")
        self.assertEqual(
            loaded["dataset"]["samples"][0]["annotation"]["gold"]["ticker"],
            "TEST",
        )

    def test_subsequent_save_preserves_previous_annotation(self):
        self.store.save_annotation(
            "eval_0001",
            _annotation(status="completed"),
        )

        self.store.save_annotation("eval_0002", _annotation())

        loaded = self.store.load()
        self.assertEqual(
            loaded["dataset"]["samples"][0]["annotation"]["status"],
            "completed",
        )

    def test_completed_annotation_rejects_pending_field(self):
        invalid = _annotation(status="completed")
        invalid["field_availability"]["currency"] = "pending"

        with self.assertRaisesRegex(ValueError, "cannot be pending"):
            self.store.save_annotation("eval_0001", invalid)

    def test_completed_annotation_requires_answerable_gold(self):
        invalid = _annotation(status="completed")
        invalid["gold"]["ticker"] = None

        with self.assertRaisesRegex(ValueError, "requires a gold value"):
            self.store.save_annotation("eval_0001", invalid)

    def test_load_migrates_legacy_issuer_annotation(self):
        legacy = json.loads(self.input_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = "1.0"
        legacy["metadata"]["critical_fields"] = [
            "ticker",
            "issuer",
            "currency",
            "maturity_date",
            "coupon_type",
            "coupon_rate",
        ]
        legacy_sample = legacy["samples"][0]
        legacy_sample["missing_fields_current"] = ["issuer", "currency"]
        legacy_sample["missing_field_count"] = 2
        legacy_sample["difficulty_candidate"] = "missing_2_3"
        legacy_sample["annotation"]["field_availability"]["issuer"] = "absent"
        legacy_sample["annotation"]["gold"]["issuer"] = None
        self.input_path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = self.store.load()["dataset"]
        migrated_sample = loaded["samples"][0]

        self.assertEqual(loaded["schema_version"], "1.1")
        self.assertNotIn(
            "issuer",
            loaded["metadata"]["critical_fields"],
        )
        self.assertEqual(
            migrated_sample["missing_fields_current"],
            ["currency"],
        )
        self.assertEqual(
            migrated_sample["difficulty_candidate"],
            "missing_1",
        )
        self.assertNotIn(
            "issuer",
            migrated_sample["annotation"]["field_availability"],
        )


if __name__ == "__main__":
    unittest.main()
