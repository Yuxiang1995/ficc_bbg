import copy
import unittest

from ficc_bbg_parser.models import ExtractionFailure, QuoteRecord

from .run_inference import run_inference


def _sample(sample_id, quote_line, paragraph_id="msg_1"):
    return {
        "sample_id": sample_id,
        "source": {
            "run_id": 1,
            "paragraph_id": paragraph_id,
            "quote_time": "2026-07-01T01:02:03+00:00",
            "quote_source": "SCB",
            "speaker": "Dealer",
            "source_date": "2026-07-01",
        },
        "input": {
            "quote_line": quote_line,
            "paragraph_context": "USD offers\nSecurity Px",
        },
        "prediction_snapshot": [{"ticker": "OLD"}],
        "extraction_failure": {"error_type": "HistoricalFailure"},
        "annotation": {
            "status": "completed",
            "gold": {"ticker": "GOLD"},
        },
    }


class FakeExtractor:
    model = "fake-model"
    batch_size = 10
    max_workers = 2
    max_retries = 2
    request_timeout = 90

    def __init__(self):
        self.failures = []
        self.seen_quote_lines = []
        self.seen_paragraphs = []

    def extract(self, quote_lines, paragraphs):
        self.seen_quote_lines = quote_lines
        self.seen_paragraphs = paragraphs
        records = []
        for line in quote_lines:
            line_no = int(line.line_id.rsplit("_", 1)[1])
            records.append(
                QuoteRecord(
                    ticker=line.text.split()[0],
                    message_id=line.paragraph_id,
                    line_no=line_no,
                    raw_line=line.text,
                    remark=f"line_id={line.line_id}",
                )
            )
        return records


class FailureExtractor(FakeExtractor):
    def extract(self, quote_lines, paragraphs):
        self.seen_quote_lines = quote_lines
        self.seen_paragraphs = paragraphs
        failed = quote_lines[1]
        self.failures = [
            ExtractionFailure(
                line_id=failed.line_id,
                paragraph_id=failed.paragraph_id,
                raw_line=failed.text,
                model=self.model,
                attempts=3,
                error_type="TimeoutError",
                error_message="timed out",
            )
        ]
        first = quote_lines[0]
        return [
            QuoteRecord(
                ticker="FIRST",
                message_id=first.paragraph_id,
                line_no=int(first.line_id.rsplit("_", 1)[1]),
                raw_line=first.text,
                remark=f"line_id={first.line_id}",
            )
        ]


class RunInferenceTest(unittest.TestCase):
    def test_predictions_are_refreshed_and_annotations_are_preserved(self):
        dataset = {
            "metadata": {"seed": 1},
            "samples": [
                _sample("eval_0001", "AAA 5.0 2030 100/101"),
                _sample("eval_0002", "BBB 4.0 2031 99/100"),
            ],
        }
        original = copy.deepcopy(dataset)
        extractor = FakeExtractor()

        result = run_inference(dataset, extractor)

        self.assertEqual(dataset, original)
        self.assertEqual(
            [item["annotation"] for item in result["samples"]],
            [item["annotation"] for item in original["samples"]],
        )
        self.assertEqual(
            result["samples"][0]["prediction_snapshot"][0]["ticker"],
            "AAA",
        )
        self.assertEqual(
            result["samples"][1]["prediction_snapshot"][0]["ticker"],
            "BBB",
        )
        self.assertEqual(result["samples"][0]["inference_status"], "predicted")
        self.assertEqual(len(extractor.seen_paragraphs), 1)
        self.assertEqual(len(extractor.seen_quote_lines), 2)
        self.assertEqual(
            result["metadata"]["inference"]["status_counts"],
            {"predicted": 2},
        )
        self.assertTrue(
            result["metadata"]["inference"]["annotation_preserved"]
        )
        self.assertEqual(
            result["metadata"]["inference"]["unmapped_failures"],
            [],
        )

    def test_failure_is_recorded_without_replacing_historical_failure(self):
        dataset = {
            "samples": [
                _sample("eval_0001", "AAA 5.0 2030 100/101"),
                _sample("eval_0002", "BBB 4.0 2031 99/100"),
            ]
        }

        result = run_inference(dataset, FailureExtractor())

        first, second = result["samples"]
        self.assertEqual(first["inference_status"], "predicted")
        self.assertEqual(second["inference_status"], "failed")
        self.assertEqual(second["prediction_snapshot"], [])
        self.assertEqual(
            second["inference_failure"][0]["error_type"],
            "TimeoutError",
        )
        self.assertEqual(
            second["extraction_failure"],
            {"error_type": "HistoricalFailure"},
        )

    def test_same_paragraph_id_from_different_runs_is_not_grouped(self):
        first = _sample("eval_0001", "AAA 5.0 2030 100/101")
        second = _sample("eval_0002", "BBB 4.0 2031 99/100")
        second["source"]["run_id"] = 2
        extractor = FakeExtractor()

        run_inference({"samples": [first, second]}, extractor)

        self.assertEqual(len(extractor.seen_paragraphs), 2)


if __name__ == "__main__":
    unittest.main()
