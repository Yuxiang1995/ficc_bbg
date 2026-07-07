import json
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

from openai import OpenAI

from .models import EXPORT_COLUMNS, MessageBlock, QuoteRecord
from .normalization import extract_title


DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


QUOTE_SCHEMA: Dict[str, Any] = {
    "name": "bond_quote_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "quotes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ticker": {"type": ["string", "null"]},
                        "isin": {"type": ["string", "null"]},
                        "issuer": {"type": ["string", "null"]},
                        "currency": {"type": ["string", "null"]},
                        "maturity_date": {"type": ["string", "null"]},
                        "coupon_type": {"type": ["string", "null"], "enum": ["Fixed", "FRN", None]},
                        "coupon_rate": {"type": ["number", "null"]},
                        "rating": {"type": ["string", "null"]},
                        "bid_price": {"type": ["number", "null"]},
                        "ask_price": {"type": ["number", "null"]},
                        "spread_bid_ask": {"type": ["number", "null"]},
                        "bid_size": {"type": ["number", "null"]},
                        "ask_size": {"type": ["number", "null"]},
                        "quote_time": {"type": ["string", "null"]},
                        "quote_source": {"type": ["string", "null"]},
                        "bond_type": {"type": ["string", "null"], "enum": ["Primary", "Secondary", None]},
                        "region": {"type": ["string", "null"]},
                        "remark": {"type": ["string", "null"]},
                        "quote_type": {
                            "type": ["string", "null"],
                            "enum": ["price", "spread", "yield", "offer", "bid", None],
                        },
                        "bid_spread": {"type": ["number", "null"]},
                        "ask_spread": {"type": ["number", "null"]},
                        "message_id": {"type": "string"},
                        "message_type": {
                            "type": "string",
                            "enum": [
                                "quote_single",
                                "quote_list",
                                "primary_update",
                                "market_commentary",
                                "non_bond",
                                "unknown",
                            ],
                        },
                        "line_no": {"type": ["integer", "null"]},
                        "raw_line": {"type": ["string", "null"]},
                        "parser_name": {"type": ["string", "null"]},
                        "parse_status": {
                            "type": "string",
                            "enum": ["parsed", "needs_review", "skipped"],
                        },
                        "confidence": {"type": "number"},
                    },
                    "required": EXPORT_COLUMNS,
                },
            }
        },
        "required": ["quotes"],
    },
}


SYSTEM_PROMPT = """You extract institutional bond quotes from Bloomberg chat/email text.

Return one JSON object that matches the supplied schema. Put one item in quotes for every
actual bond quote that should be reviewed or stored. If a message is a market brief,
deal statistics, CD run/deposit table, roadshow logistics, orderbook commentary, or other
non-bond-quote content, return no quote for it.

Important extraction rules:
- Preserve source values. Do not invent ISIN, issuer full name, rating, currency, or region.
- Use null when a field is unknown or not explicitly implied by the text.
- quote_time should use the message quote_time supplied in the input unless a line has a more specific time.
- quote_source should identify the speaker and message title/source.
- bond_type is Primary for new issue/primary market lines, otherwise Secondary.
- quote_type is price for price pairs, spread for spread pairs in bp, offer for offer-only axes/lists, bid for bid-only, yield for yield-only.
- Price quotes use bid_price and ask_price. Spread quotes use bid_spread and ask_spread.
- For spread pairs, spread_bid_ask is the absolute bid/ask spread difference in bp.
- For size pairs like 25x10mm, bid_size is 25 and ask_size is 10.
- For abbreviated price pairs like 100.25/40, ask_price is 100.40.
- Convert fractions like 4⅜ to decimal coupon_rate 4.375.
- coupon_type is FRN for Float/FRN/SOFR floating-rate bonds; Fixed for fixed coupon bonds.
- Normalize maturity_date to YYYY-MM-DD when day is present, YYYY-MM when only month/year is present, or YYYY when only year is present.
- raw_line should be the original line or compact row text that produced the quote.
- confidence is 0 to 1. Use needs_review when a quote is real but key fields are ambiguous.
"""


class OpenAIQuoteExtractor:
    def __init__(
        self,
        model: Optional[str] = None,
        batch_size: int = 8,
        max_retries: int = 2,
        show_progress: bool = True,
        base_url: Optional[str] = None,
        client: Optional[OpenAI] = None,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.show_progress = show_progress
        self.client = client or OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
        )

    def extract(self, messages: List[MessageBlock]) -> List[QuoteRecord]:
        records: List[QuoteRecord] = []
        batches = list(_chunks(messages, self.batch_size))

        for batch_index, batch in _progress_batches(batches, self.show_progress):
            started_at = time.time()
            payload = [_message_payload(message) for message in batch]
            response = self._call_model(payload)
            batch_records = _records_from_payload(response, batch)
            records.extend(batch_records)
            _report_batch_done(
                batch_index=batch_index,
                total_batches=len(batches),
                batch_size=len(batch),
                batch_records=len(batch_records),
                total_records=len(records),
                elapsed_seconds=time.time() - started_at,
                enabled=self.show_progress,
            )
        return records

    def _call_model(self, payload: List[Dict[str, Any]]) -> Dict[str, Any]:
        user_content = json.dumps({"messages": payload}, ensure_ascii=False, indent=2)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_schema", "json_schema": QUOTE_SCHEMA},
                    temperature=0,
                    timeout=30,
                )
                content = completion.choices[0].message.content
                if content is None:
                    raise ValueError("OpenAI response did not include message content")
                return json.loads(content)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(2**attempt)

        raise RuntimeError(f"OpenAI quote extraction failed: {last_error}") from last_error


def _records_from_payload(payload: Dict[str, Any], messages: List[MessageBlock]) -> List[QuoteRecord]:
    messages_by_id = {message.message_id: message for message in messages}
    records: List[QuoteRecord] = []

    for item in payload.get("quotes", []):
        if item.get("parse_status") == "skipped":
            continue

        record = QuoteRecord(**{column: item.get(column) for column in EXPORT_COLUMNS})
        message = messages_by_id.get(record.message_id or "")

        if message is not None:
            record.quote_time = record.quote_time or message.quote_time
            record.quote_source = record.quote_source or _default_quote_source(message)

        record.parser_name = "openai_llm"
        record.parse_status = record.parse_status or "parsed"
        record.confidence = float(record.confidence or 0)

        if record.quote_type == "spread" and record.spread_bid_ask is None:
            if record.bid_spread is not None and record.ask_spread is not None:
                record.spread_bid_ask = abs(record.bid_spread - record.ask_spread)

        records.append(record)

    return records


def _message_payload(message: MessageBlock) -> Dict[str, Any]:
    return {
        "message_id": message.message_id,
        "message_time": message.message_time,
        "quote_time": message.quote_time,
        "speaker": message.speaker,
        "quote_source_hint": _default_quote_source(message),
        "text": message.text,
    }


def _default_quote_source(message: MessageBlock) -> str:
    title = extract_title(message.text)
    if title:
        return f"{message.speaker} | {title}"
    return message.speaker


def _chunks(values: List[MessageBlock], size: int) -> Iterable[List[MessageBlock]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _progress_batches(batches: List[List[MessageBlock]], enabled: bool):
    if not enabled:
        return enumerate(batches, start=1)

    try:
        from tqdm import tqdm
    except ImportError:
        return enumerate(batches, start=1)

    return enumerate(tqdm(batches, desc="LLM extract", unit="batch"), start=1)


def _report_batch_done(
    batch_index: int,
    total_batches: int,
    batch_size: int,
    batch_records: int,
    total_records: int,
    elapsed_seconds: float,
    enabled: bool,
) -> None:
    if not enabled:
        return

    # tqdm gives the visual progress bar. This fallback keeps useful progress visible
    # when tqdm is not installed, and also leaves a compact per-batch timing trace.
    print(
        "batch "
        f"{batch_index}/{total_batches}: "
        f"messages={batch_size}, "
        f"quotes={batch_records}, "
        f"total_quotes={total_records}, "
        f"elapsed={elapsed_seconds:.1f}s",
        file=sys.stderr,
    )
