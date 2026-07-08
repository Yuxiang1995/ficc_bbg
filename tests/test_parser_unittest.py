import unittest

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

    def extract(self, quote_lines):
        self.quote_lines = quote_lines
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


if __name__ == "__main__":
    unittest.main()
