from typing import List, Optional, Protocol, Tuple

from .models import MessageBlock, QuoteRecord
from .splitter import split_messages


class QuoteExtractor(Protocol):
    def extract(self, messages: List[MessageBlock]) -> List[QuoteRecord]:
        ...


def parse_chat_text(
    raw_text: str,
    source_date: Optional[str] = None,
    extractor: Optional[QuoteExtractor] = None,
) -> Tuple[List[MessageBlock], List[QuoteRecord]]:
    messages = split_messages(raw_text, source_date=source_date)
    if extractor is None:
        from .llm_extractor import OpenAIQuoteExtractor

        extractor = OpenAIQuoteExtractor()
    return messages, extractor.extract(messages)
