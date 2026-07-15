from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


REQUESTED_COLUMNS = [
    "ticker",
    "isin",
    "issuer",
    "currency",
    "maturity_date",
    "coupon_type",
    "coupon_rate",
    "rating",
    "bid_price",
    "ask_price",
    "spread_bid_ask",
    "bid_size",
    "ask_size",
    "quote_time",
    "quote_source",
    "bond_type",
    "region",
    "remark",
]

DEBUG_COLUMNS = [
    "quote_type",
    "bid_spread",
    "ask_spread",
    "message_id",
    "message_type",
    "line_no",
    "raw_line",
    "parser_name",
    "parse_status",
    "confidence",
]

EXPORT_COLUMNS = REQUESTED_COLUMNS + DEBUG_COLUMNS


@dataclass
class ParagraphBlock:
    paragraph_id: str
    paragraph_type: str
    text: str
    quote_time: Optional[str] = None
    quote_source: Optional[str] = None
    speaker: Optional[str] = None
    source_date: Optional[str] = None
    confidence: float = 0.0


@dataclass
class QuoteLine:
    line_id: str
    paragraph_id: str
    text: str
    quote_time: Optional[str] = None
    quote_source: Optional[str] = None
    speaker: Optional[str] = None
    source_date: Optional[str] = None


@dataclass
class ExtractionFailure:
    line_id: str
    paragraph_id: str
    raw_line: str
    model: str
    attempts: int
    error_type: str
    error_message: str
    quote_time: Optional[str] = None
    quote_source: Optional[str] = None
    speaker: Optional[str] = None
    source_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuoteRecord:
    ticker: Optional[str] = None
    isin: Optional[str] = None
    issuer: Optional[str] = None
    currency: Optional[str] = None
    maturity_date: Optional[str] = None
    coupon_type: Optional[str] = None
    coupon_rate: Optional[float] = None
    rating: Optional[str] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    spread_bid_ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    quote_time: Optional[str] = None
    quote_source: Optional[str] = None
    bond_type: Optional[str] = None
    region: Optional[str] = None
    remark: Optional[str] = None
    quote_type: Optional[str] = None
    bid_spread: Optional[float] = None
    ask_spread: Optional[float] = None
    message_id: Optional[str] = None
    message_type: Optional[str] = None
    line_no: Optional[int] = None
    raw_line: Optional[str] = None
    parser_name: Optional[str] = None
    parse_status: str = "parsed"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return {column: data.get(column) for column in EXPORT_COLUMNS}
