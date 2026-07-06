import unittest

from ficc_bbg_parser.models import QuoteRecord
from ficc_bbg_parser.pipeline import parse_chat_text


class FakeLLMExtractor:
    def __init__(self):
        self.messages = []

    def extract(self, messages):
        self.messages = messages
        first = messages[0]
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
                quote_source=f"{first.speaker} | LLM fixture",
                bond_type="Secondary",
                message_id=first.message_id,
                message_type="quote_single",
                line_no=1,
                raw_line="IIFOIN 7.6 29 >> 100.15/100.30",
                parser_name="fake_llm",
                parse_status="parsed",
                confidence=0.99,
            )
        ]


class ParserTest(unittest.TestCase):
    def test_pipeline_uses_injected_llm_extractor(self):
        raw = "08:40:20  TOMMY HO : IIFOIN 7.6 29 >> 100.15/100.30"
        extractor = FakeLLMExtractor()

        messages, records = parse_chat_text(raw, source_date="2026-06-04", extractor=extractor)

        self.assertEqual(len(messages), 1)
        self.assertEqual(len(extractor.messages), 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].ticker, "IIFOIN")
        self.assertEqual(records[0].bid_price, 100.15)
        self.assertEqual(records[0].ask_price, 100.30)
        self.assertEqual(records[0].quote_time, "2026-06-04 08:40:20")
        self.assertEqual(records[0].parser_name, "fake_llm")

    def test_pipeline_passes_non_quote_messages_to_llm(self):
        raw = """08:10:31  TOMMY HO : market comment only
08:40:20  TOMMY HO : IIFOIN 7.6 29 >> 100.15/100.30"""
        extractor = FakeLLMExtractor()

        messages, _ = parse_chat_text(raw, extractor=extractor)

        self.assertEqual(len(messages), 2)
        self.assertEqual([message.message_id for message in extractor.messages], ["msg_00001", "msg_00002"])


if __name__ == "__main__":
    unittest.main()
