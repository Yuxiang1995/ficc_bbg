import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

import httpx
from openai import OpenAI

from .json_utils import load_json_object
from .models import EXPORT_COLUMNS, QuoteLine, QuoteRecord
from .settings import DEFAULT_BASE_URL, MID_MODEL



SYSTEM_PROMPT = """You extract institutional bond quotes from single-line quote statements.

Return only one valid JSON object. Do not wrap it in markdown. Do not include explanatory text.

The JSON object must have this shape:
{
  "quotes": [
    {
      "ticker": string or null,
      "isin": string or null,
      "issuer": string or null,
      "currency": string or null,
      "maturity_date": string or null,
      "coupon_type": "Fixed" or "FRN" or null,
      "coupon_rate": number or null,
      "rating": string or null,
      "bid_price": number or null,
      "ask_price": number or null,
      "spread_bid_ask": number or null,
      "bid_size": number or null,
      "ask_size": number or null,
      "quote_time": null,
      "quote_source": null,
      "bond_type": "Primary" or "Secondary" or null,
      "region": string or null,
      "remark": string or null,
      "quote_type": "price" or "spread" or "yield" or "offer" or "bid" or null,
      "message_id": null,
      "message_type": "quote_single" or "quote_list" or "unknown",
      "line_no": integer,
      "raw_line": string,
      "parser_name": null,
      "parse_status": "parsed" or "needs_review" or "skipped",
      "confidence": number
    }
  ]
}

Put one item in quotes for every
input line that contains an actual bond quote. The user message contains only quote
statements, separated by newline characters. Do not use any context outside each line.

Important extraction rules:
- Preserve source values. Do not invent ISIN, issuer full name, rating, currency, or region.
- Use null when a field is unknown or not explicitly implied by the text.
- line_no must be the 1-based line number in the current input batch.
- message_id, quote_time, and quote_source may be null; local post-processing will fill them.
- bond_type is Primary for new issue/primary market lines, otherwise Secondary.
- quote_type is price for price pairs, spread for spread pairs in bp, offer for offer-only axes/lists, bid for bid-only, yield for yield-only.
- Price quotes use bid_price and ask_price.
- For spread pairs, spread_bid_ask is the absolute bid/ask spread difference in bp.
- For size pairs like 25x10mm, bid_size is 25 and ask_size is 10.
- For abbreviated price pairs like 100.25/40, ask_price is 100.40.
- Convert fractions like 4⅜ to decimal coupon_rate 4.375.
- coupon_type is FRN for Float/FRN/SOFR floating-rate bonds; Fixed for fixed coupon bonds.
- Normalize maturity_date to YYYY-MM-DD when day is present, YYYY-MM when only month/year is present, or YYYY when only year is present.
- raw_line should be the exact input line that produced the quote.
- confidence is 0 to 1. Use needs_review when a quote is real but key fields are ambiguous.
"""


class OpenAIQuoteExtractor:
    def __init__(
        self,
        model: Optional[str] = None,
        batch_size: int = 8,
        max_retries: int = 1,
        request_timeout: float = 30,
        show_progress: bool = True,
        base_url: Optional[str] = None,
        client: Optional[OpenAI] = None,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL") or MID_MODEL
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.show_progress = show_progress
        self.client = client or OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
            max_retries=0,
            timeout=_http_timeout(request_timeout),
        )

    def extract(self, quote_lines: List[QuoteLine]) -> List[QuoteRecord]:
        records: List[QuoteRecord] = []
        batches = list(_chunks(quote_lines, self.batch_size))

        for batch_index, batch in _progress_batches(batches, self.show_progress):
            started_at = time.time()
            response = self._call_model(batch)
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

    def _call_model(self, quote_lines: List[QuoteLine]) -> Dict[str, Any]:
        user_content = "\n".join(line.text for line in quote_lines)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                _report_attempt(
                    label="extract",
                    attempt=attempt + 1,
                    max_attempts=self.max_retries + 1,
                    timeout_seconds=self.request_timeout,
                    enabled=self.show_progress,
                )
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    extra_body={"enable_thinking": False},
                    temperature=0,
                    timeout=_http_timeout(self.request_timeout),
                )
                content = completion.choices[0].message.content
                if content is None:
                    raise ValueError("OpenAI response did not include message content")
                return load_json_object(content)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                # time.sleep(2**attempt)

        raise RuntimeError(f"OpenAI quote extraction failed: {last_error}") from last_error


def _records_from_payload(payload: Dict[str, Any], quote_lines: List[QuoteLine]) -> List[QuoteRecord]:
    records: List[QuoteRecord] = []

    for item in payload.get("quotes", []):
        if item.get("parse_status") == "skipped":
            continue

        record = QuoteRecord(**{column: item.get(column) for column in EXPORT_COLUMNS})
        quote_line = _quote_line_for_record(record, quote_lines)

        if quote_line is not None:
            record.message_id = quote_line.paragraph_id
            record.line_no = _global_line_no(quote_line)
            record.raw_line = quote_line.text
            record.quote_time = quote_line.quote_time
            record.quote_source = quote_line.quote_source
            if record.remark:
                record.remark = f"{record.remark}; line_id={quote_line.line_id}"
            else:
                record.remark = f"line_id={quote_line.line_id}"

        record.parser_name = "openai_llm"
        record.parse_status = record.parse_status or "parsed"
        record.confidence = float(record.confidence or 0)

        if record.quote_type == "spread" and record.spread_bid_ask is None:
            if record.bid_spread is not None and record.ask_spread is not None:
                record.spread_bid_ask = abs(record.bid_spread - record.ask_spread)

        records.append(record)

    return records


def _quote_line_for_record(record: QuoteRecord, quote_lines: List[QuoteLine]) -> Optional[QuoteLine]:
    if record.line_no is None:
        return None
    index = record.line_no - 1
    if 0 <= index < len(quote_lines):
        return quote_lines[index]
    return None


def _global_line_no(quote_line: QuoteLine) -> Optional[int]:
    try:
        return int(quote_line.line_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _chunks(values: List[QuoteLine], size: int) -> Iterable[List[QuoteLine]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _progress_batches(batches: List[List[QuoteLine]], enabled: bool):
    if not enabled:
        return enumerate(batches, start=1)

    try:
        from tqdm import tqdm
    except ImportError:
        return enumerate(batches, start=1)

    return enumerate(tqdm(batches, desc="LLM extract", unit="batch"), start=1)


def _report_attempt(
    label: str,
    attempt: int,
    max_attempts: int,
    timeout_seconds: float,
    enabled: bool,
) -> None:
    if not enabled:
        return

    print(
        f"{label} request attempt {attempt}/{max_attempts}: "
        f"timeout={timeout_seconds:.0f}s",
        file=sys.stderr,
    )


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


def _http_timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=seconds,
        connect=min(10.0, seconds),
        read=seconds,
        write=min(30.0, seconds),
        pool=min(10.0, seconds),
    )
