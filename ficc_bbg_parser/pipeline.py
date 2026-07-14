from typing import List, Optional, Protocol, Tuple

from .models import ParagraphBlock, QuoteLine, QuoteRecord


class ParagraphSplitter(Protocol):
    def split(self, raw_text: str, source_date: Optional[str] = None) -> Tuple[List[ParagraphBlock], List[QuoteLine]]:
        ...


class QuoteExtractor(Protocol):
    def extract(
        self,
        quote_lines: List[QuoteLine],
        paragraphs: Optional[List[ParagraphBlock]] = None,
    ) -> List[QuoteRecord]:
        ...


def parse_chat_text(
    raw_text: str,
    source_date: Optional[str] = None,
    splitter: Optional[ParagraphSplitter] = None,
    extractor: Optional[QuoteExtractor] = None,
) -> Tuple[List[ParagraphBlock], List[QuoteLine], List[QuoteRecord]]:
    if splitter is None:
        from .llm_splitter import OpenAIParagraphSplitter

        splitter = OpenAIParagraphSplitter()
    if extractor is None:
        from .llm_extractor import OpenAIQuoteExtractor

        extractor = OpenAIQuoteExtractor()

    paragraphs, quote_lines = splitter.split(raw_text, source_date=source_date)
    return paragraphs, quote_lines, extractor.extract(quote_lines, paragraphs=paragraphs)
