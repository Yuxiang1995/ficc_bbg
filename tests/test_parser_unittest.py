import unittest
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ficc_bbg_parser.models import ParagraphBlock, QuoteLine, QuoteRecord
from ficc_bbg_parser.pipeline import parse_chat_text


class FakeLLMSplitter:
    def __init__(self):
        self.raw_text = None
        self.source_date = None

    def split(self, raw_text, source_date=None):
        self.raw_text = raw_text
        self.source_date = source_date
        paragraphs = [
            ParagraphBlock(
                paragraph_id="para_00001",
                paragraph_type="quote",
                text="IIFOIN 7.6 29 >> 100.15/100.30",
                quote_time="2026-06-04 08:40:20",
                quote_source="TOMMY HO",
                speaker="TOMMY HO",
                source_date=source_date,
                confidence=0.99,
            )
        ]
        quote_lines = [
            QuoteLine(
                line_id="line_00001",
                paragraph_id="para_00001",
                text="IIFOIN 7.6 29 >> 100.15/100.30",
                quote_time="2026-06-04 08:40:20",
                quote_source="TOMMY HO",
                speaker="TOMMY HO",
                source_date=source_date,
            )
        ]
        return paragraphs, quote_lines


class FakeLLMExtractor:
    def __init__(self):
        self.quote_lines = []
        self.paragraphs = []

    def extract(self, quote_lines, paragraphs=None):
        self.quote_lines = quote_lines
        self.paragraphs = paragraphs or []
        first = quote_lines[0]
        return [
            QuoteRecord(
                ticker="IIFOIN",
                coupon_rate=7.6,
                coupon_type="Fixed",
                maturity_date="2029",
                bid_price=100.15,
                ask_price=100.30,
                quote_type="price",
                quote_time=first.quote_time,
                quote_source=first.quote_source,
                bond_type="Secondary",
                message_id=first.paragraph_id,
                message_type="quote_single",
                line_no=1,
                raw_line=first.text,
                parser_name="fake_llm",
                parse_status="parsed",
                confidence=0.99,
            )
        ]


class ParserTest(unittest.TestCase):
    def test_full_rerun_builds_one_cli_command_per_source_date(self):
        from ficc_bbg_parser.rerun_full import _parse_args, build_cli_command

        args = _parse_args([
            "--start-date", "2026-06-01",
            "--end-date", "2026-06-03",
            "--output-mode", "both",
            "--out-root", "output/rerun-test",
            "--extract-workers", "6",
        ])

        command = build_cli_command(args, date(2026, 6, 2))

        self.assertIn("ficc_bbg_parser.cli", command)
        self.assertEqual(command[command.index("--receive-date") + 1], "2026-06-02")
        self.assertEqual(command[command.index("--extract-workers") + 1], "6")
        self.assertEqual(
            Path(command[command.index("--out-dir") + 1]),
            Path("output/rerun-test/2026-06-02"),
        )

    def test_full_rerun_rejects_reversed_date_range(self):
        from ficc_bbg_parser.rerun_full import _parse_args

        with self.assertRaises(SystemExit):
            _parse_args([
                "--start-date", "2026-06-04",
                "--end-date", "2026-06-03",
            ])

    def test_llm_extractor_prompt_preserves_market_conventions(self):
        from ficc_bbg_parser.llm_extractor import SYSTEM_PROMPT

        normalized_prompt = " ".join(SYSTEM_PROMPT.split())
        required_rules = [
            "Headers and column order are open-ended",
            "this is not an exhaustive list",
            "timestamp and speaker/quoting-person prefix are meaningful source metadata",
            "25x10mm => bid_size=25, ask_size=10",
            "Never multiply by 1,000",
            "MTRC 34 €99.05/99.25",
            "CDCAUS 6/56 FRN",
            "trading more here",
        ]

        for rule in required_rules:
            self.assertIn(rule, normalized_prompt)

    def test_pipeline_uses_injected_llm_splitter_and_extractor(self):
        raw = "08:40:20  TOMMY HO : IIFOIN 7.6 29 >> 100.15/100.30"
        splitter = FakeLLMSplitter()
        extractor = FakeLLMExtractor()

        paragraphs, quote_lines, records = parse_chat_text(
            raw,
            source_date="2026-06-04",
            splitter=splitter,
            extractor=extractor,
        )

        self.assertEqual(splitter.raw_text, raw)
        self.assertEqual(splitter.source_date, "2026-06-04")
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(len(quote_lines), 1)
        self.assertEqual(len(extractor.quote_lines), 1)
        self.assertEqual(extractor.paragraphs, paragraphs)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].ticker, "IIFOIN")
        self.assertEqual(records[0].bid_price, 100.15)
        self.assertEqual(records[0].ask_price, 100.30)
        self.assertEqual(records[0].quote_time, "2026-06-04 08:40:20")
        self.assertEqual(records[0].raw_line, "IIFOIN 7.6 29 >> 100.15/100.30")

    def test_llm_splitter_classifies_raw_messages_with_fake_client(self):
        try:
            from ficc_bbg_parser.llm_splitter import OpenAIParagraphSplitter
        except ImportError as exc:
            self.skipTest(f"OpenAI dependency is not installed: {exc}")

        raw = """08:40:20  TOMMY HO : IIFOIN 7.6 29 >> 100.15/100.30
08:41:00  TOMMY HO : market comment only"""
        splitter = OpenAIParagraphSplitter(client=FakeOpenAIClient(), show_progress=False)

        paragraphs, quote_lines = splitter.split(raw, source_date="2026-06-04")

        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(paragraphs[0].paragraph_type, "quote")
        self.assertEqual(paragraphs[1].paragraph_type, "commentary")
        self.assertEqual(len(quote_lines), 1)
        self.assertEqual(quote_lines[0].text, "08:40:20 TOMMY HO : IIFOIN 7.6 29 >> 100.15/100.30")
        self.assertEqual(quote_lines[0].quote_time, "2026-06-04 08:40:20")

    def test_llm_extractor_parallel_preserves_batch_order(self):
        try:
            from ficc_bbg_parser.llm_extractor import OpenAIQuoteExtractor
        except ImportError as exc:
            self.skipTest(f"OpenAI dependency is not installed: {exc}")

        quote_lines = [
            QuoteLine(line_id="line_00001", paragraph_id="para_00001", text="AAA 1 01/01/30 100/101"),
            QuoteLine(line_id="line_00002", paragraph_id="para_00002", text="BBB 2 01/01/31 99/100"),
            QuoteLine(line_id="line_00003", paragraph_id="para_00003", text="CCC 3 01/01/32 98/99"),
        ]
        extractor = OpenAIQuoteExtractor(
            client=FakeExtractorClient(),
            batch_size=1,
            max_workers=3,
            show_progress=False,
        )

        records = extractor.extract(quote_lines)

        self.assertEqual([record.ticker for record in records], ["AAA", "BBB", "CCC"])
        self.assertEqual([record.raw_line for record in records], [line.text for line in quote_lines])

    def test_llm_extractor_batches_by_paragraph_and_batch_size(self):
        try:
            from ficc_bbg_parser.llm_extractor import _paragraph_batches
        except ImportError as exc:
            self.skipTest(f"OpenAI dependency is not installed: {exc}")

        paragraphs = [
            ParagraphBlock("para_00001", "quote_list", "paragraph one"),
            ParagraphBlock("para_00002", "quote", "paragraph two"),
        ]
        quote_lines = [
            QuoteLine(f"line_{idx:05d}", "para_00001", f"AAA {idx}")
            for idx in range(1, 4)
        ] + [QuoteLine("line_00004", "para_00002", "BBB 4")]

        batches = _paragraph_batches(quote_lines, paragraphs, batch_size=2)

        self.assertEqual([batch.paragraph_id for batch in batches], ["para_00001", "para_00001", "para_00002"])
        self.assertEqual([len(batch.quote_lines) for batch in batches], [2, 1, 1])
        self.assertIs(batches[0].paragraph, paragraphs[0])

    def test_llm_extractor_sends_paragraph_once_as_auxiliary_context(self):
        try:
            from ficc_bbg_parser.llm_extractor import OpenAIQuoteExtractor
        except ImportError as exc:
            self.skipTest(f"OpenAI dependency is not installed: {exc}")

        client = RecordingExtractorClient()
        paragraph_lines = [
            "USD senior list",
            "header two",
            "header three",
            "header four",
            "header five",
            "header six",
            "AAA 1 2030 100/101",
        ]
        paragraph = ParagraphBlock("para_00001", "quote_list", "\n".join(paragraph_lines))
        quote_line = QuoteLine("line_00001", "para_00001", "AAA 1 2030 100/101")
        extractor = OpenAIQuoteExtractor(client=client, show_progress=False)

        extractor.extract([quote_line], paragraphs=[paragraph])

        user_payload = json.loads(client.completions.user_content)
        self.assertEqual(user_payload["paragraph_context"]["text"], "\n".join(paragraph_lines[:5]))
        self.assertEqual(user_payload["quote_lines"], [
            {"line_no": 1, "line_id": "line_00001", "text": quote_line.text}
        ])

    def test_llm_extractor_records_failed_lines_after_three_attempts(self):
        try:
            from ficc_bbg_parser.llm_extractor import OpenAIQuoteExtractor
        except ImportError as exc:
            self.skipTest(f"OpenAI dependency is not installed: {exc}")

        client = SelectiveFailingExtractorClient()
        quote_lines = [
            QuoteLine("line_00001", "para_00001", "FAIL 1 2030 100/101"),
            QuoteLine("line_00002", "para_00002", "GOOD 2 2031 99/100"),
        ]
        extractor = OpenAIQuoteExtractor(
            client=client,
            batch_size=1,
            max_workers=1,
            max_retries=2,
            show_progress=False,
        )

        with patch("ficc_bbg_parser.llm_extractor.time.sleep"):
            records = extractor.extract(quote_lines)

        self.assertEqual([record.ticker for record in records], ["GOOD"])
        self.assertEqual(client.completions.failed_calls, 3)
        self.assertEqual(len(extractor.failures), 1)
        failure = extractor.failures[0]
        self.assertEqual(failure.line_id, "line_00001")
        self.assertEqual(failure.attempts, 3)
        self.assertEqual(failure.error_type, "TimeoutError")
        self.assertIn("simulated timeout", failure.error_message)

    def test_splitter_balances_message_batches_by_payload_length(self):
        try:
            from ficc_bbg_parser.llm_splitter import RawMessage, _balanced_message_batches, _batch_weight, _message_weight
        except ImportError as exc:
            self.skipTest(f"OpenAI dependency is not installed: {exc}")

        lines = [
            "08:00:00  A : " + ("long-a " * 500),
            "08:01:00  A : short-a",
            "08:02:00  A : " + ("long-b " * 500),
            "08:03:00  A : short-b",
        ]
        messages = [
            RawMessage(f"msg_{idx:05d}", f"08:0{idx}:00", "A", line, idx, idx)
            for idx, line in enumerate(lines, start=1)
        ]

        target_chars = 4000
        batches = _balanced_message_batches(messages, lines, target_chars=target_chars)
        weights = [_batch_weight(batch, lines) for batch in batches]
        total_weight = sum(_message_weight(message, lines) for message in messages)
        expected_batch_count = min(len(messages), (total_weight + target_chars - 1) // target_chars + 1)

        self.assertEqual(len(batches), expected_batch_count)
        self.assertLessEqual(max(weights), target_chars)

    def test_postgres_store_parses_partial_maturities(self):
        from ficc_bbg_parser.pg_store import _parse_datetime, _parse_maturity

        year = _parse_maturity("2031")
        month = _parse_maturity("2031-06")
        day = _parse_maturity("2031-06-30")

        self.assertEqual((year["precision"], year["year"]), ("year", 2031))
        self.assertEqual(
            (month["precision"], month["year"], month["month"]),
            ("month", 2031, 6),
        )
        self.assertEqual(day["precision"], "day")
        self.assertEqual(day["date"].isoformat(), "2031-06-30")
        self.assertEqual(
            _parse_datetime("2026-06-04 09:00:00").utcoffset().total_seconds(),
            8 * 60 * 60,
        )

    def test_postgres_store_adds_manual_input_paragraph(self):
        from ficc_bbg_parser.pg_store import _with_missing_paragraphs

        quote_lines = [
            QuoteLine("line_00001", "manual_input", "AAA 1 2030 100/101"),
            QuoteLine("line_00002", "manual_input", "BBB 2 2031 99/100"),
        ]

        paragraphs = _with_missing_paragraphs([], quote_lines)

        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0].paragraph_id, "manual_input")
        self.assertEqual(paragraphs[0].paragraph_type, "quote")
        self.assertEqual(paragraphs[0].text, "AAA 1 2030 100/101\nBBB 2 2031 99/100")


class FakeMessage:
    content = """
{
  "messages": [
    {
      "message_id": "msg_00001",
      "message_type": "quote",
      "quote_line_ranges": [{"start_line": 1, "end_line": 1}],
      "quote_source": "TOMMY HO",
      "confidence": 0.99
    },
    {
      "message_id": "msg_00002",
      "message_type": "commentary",
      "quote_line_ranges": [],
      "quote_source": "TOMMY HO",
      "confidence": 0.95
    }
  ]
}
"""


class FakeChoice:
    message = FakeMessage()


class FakeCompletion:
    choices = [FakeChoice()]


class FakeCompletions:
    def create(self, **kwargs):
        return FakeCompletion()


class FakeChat:
    completions = FakeCompletions()


class FakeOpenAIClient:
    chat = FakeChat()


class FakeExtractorCompletions:
    def create(self, **kwargs):
        user_content = kwargs["messages"][1]["content"]
        user_payload = json.loads(user_content)
        quote_line = user_payload["quote_lines"][0]
        ticker = quote_line["text"].split()[0]
        payload = {
            "quotes": [
                {
                    "ticker": ticker,
                    "isin": None,
                    "issuer": None,
                    "currency": None,
                    "maturity_date": None,
                    "coupon_type": None,
                    "coupon_rate": None,
                    "rating": None,
                    "bid_price": None,
                    "ask_price": None,
                    "spread_bid_ask": None,
                    "bid_size": None,
                    "ask_size": None,
                    "quote_time": None,
                    "quote_source": None,
                    "bond_type": "Secondary",
                    "region": None,
                    "remark": None,
                    "quote_type": "price",
                    "bid_spread": None,
                    "ask_spread": None,
                    "message_id": None,
                    "message_type": "quote_single",
                    "line_no": 1,
                    "raw_line": quote_line["text"],
                    "parser_name": None,
                    "parse_status": "parsed",
                    "confidence": 0.99,
                }
            ]
        }
        return _completion_with_content(json.dumps(payload))


class FakeExtractorChat:
    completions = FakeExtractorCompletions()


class FakeExtractorClient:
    chat = FakeExtractorChat()


class RecordingExtractorCompletions(FakeExtractorCompletions):
    def __init__(self):
        self.user_content = None

    def create(self, **kwargs):
        self.user_content = kwargs["messages"][1]["content"]
        return super().create(**kwargs)


class RecordingExtractorClient:
    def __init__(self):
        self.completions = RecordingExtractorCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()


class SelectiveFailingExtractorCompletions(FakeExtractorCompletions):
    def __init__(self):
        self.failed_calls = 0

    def create(self, **kwargs):
        user_payload = json.loads(kwargs["messages"][1]["content"])
        if user_payload["quote_lines"][0]["text"].startswith("FAIL"):
            self.failed_calls += 1
            raise TimeoutError("simulated timeout")
        return super().create(**kwargs)


class SelectiveFailingExtractorClient:
    def __init__(self):
        self.completions = SelectiveFailingExtractorCompletions()
        self.chat = type("Chat", (), {"completions": self.completions})()


def _completion_with_content(content):
    class Message:
        pass

    class Choice:
        pass

    class Completion:
        pass

    message = Message()
    message.content = content
    choice = Choice()
    choice.message = message
    completion = Completion()
    completion.choices = [choice]
    return completion


if __name__ == "__main__":
    unittest.main()
