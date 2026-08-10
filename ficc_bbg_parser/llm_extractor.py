import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import httpx
from openai import OpenAI

from .json_utils import load_json_object
from .models import ExtractionFailure, EXPORT_COLUMNS, ParagraphBlock, QuoteLine, QuoteRecord
from .settings import DEFAULT_BASE_URL, MID_MODEL



SYSTEM_PROMPT = """You extract institutional bond quotes from quote_lines.

Return only one valid JSON object. Do not wrap it in markdown or add explanatory text.

The user message contains:
- paragraph_context: up to the first five lines of the shared source paragraph, or null.
- quote_lines: the quote statements that must be extracted.

quote_lines are the extraction targets and primary source of truth. paragraph_context
is auxiliary evidence for shared headings, column definitions, currency, quote side,
units, and market context. Never create a quote from paragraph_context. Never copy a
bond-specific value from one quote line to another. When they conflict, the quote line
wins. A shared heading remains applicable to all compatible rows in that paragraph.

The JSON object must have exactly this shape:
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
      "bid_spread": number or null,
      "ask_spread": number or null,
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

Put one item in quotes for every item in quote_lines that contains an actual bond quote.

General source and metadata rules:
- Preserve source values. Use null when a value cannot be supported by the line or its
  shared context. Do not invent an ISIN, issuer full name, rating, or region.
- line_no must equal the supplied 1-based line_no in the current batch. raw_line must
  exactly equal the quote_line text.
- A chat timestamp and speaker/quoting-person prefix are meaningful source metadata and
  must remain in raw_line. Exclude them only when tokenizing the bond itself: never treat
  a timestamp, speaker name, desk tag, country tag, or emoji as ticker/coupon/maturity.
  message_id, quote_time, and quote_source may remain null because reliable local
  post-processing fills them from the source QuoteLine.
- confidence is 0 to 1. Use parse_status=needs_review when the quote is real but a key
  token or column is ambiguous. Do not force uncertain values into fields.

Parse table rows dynamically:
- Do not assume one fixed Bloomberg header. Headers and column order are open-ended and
  may change in future emails. Infer each column from its header meaning and position.
  Common aliases include Security, ISIN, Bench, Px/Price, Spd/ISpd, Yield/YTM, Sz/ASz,
  Axe, Notes, and Ratings, but this is not an exhaustive list.
- Prefix B means bid and A means ask when used in headers (for example B Px, A Px,
  B Spd, A Spd, B Sz, A Sz). In an offer or axes list, unqualified Px/Spd/ASz fields
  usually describe the ask/offer side.
- Email spacing is unreliable. Use the security-token pattern and the complete header
  order together. An unknown or unmodeled column must not shift the meaning of later
  recognized columns.
- Yield/YTM values are not prices or spreads. There is no yield output field: when a
  row also has a price, store the price and use quote_type=offer; for a yield-only quote,
  use quote_type=yield and preserve the yield text in remark.
- These examples illustrate column mapping only; they are not fixed templates:
  * Header: Security ASz(MM) A Yield A Px Yrs to Mty Notes
    Row: HKAA 2.93 06/05/34 25 2.09 105.950 8.0 Airport
    Maps to ask_size=25, ask_price=105.950, ask_spread=null.
  * Header: Security ASz(MM) A Px A YTM A ISpd Mdys/S&P/Fitch
    Row: HKAA 2.97 05/06/31 50 98.692 3.29 -3 -/AA+/-
    Maps to ask_size=50, ask_price=98.692, ask_spread=-3, and rating=-/AA+/-.
  * Header: Security ASz(M) A Spd A Px A Yield A Axe Bench ISIN
    Row: KOREA 2¼ 07/03/28 10000 3 99.13 2.690 Y OBL 2.2 28 #187 XS3095441021
    Maps to ask_size=10000, ask_spread=3, ask_price=99.13, and the stated ISIN.
  * Header: Security Bench A Spd A Px ASz(M) A Axe
    Row: TERUMO 4.488 04/28/31 5Y 61 98.618 4656 Y
    Maps to ask_spread=61, ask_price=98.618, ask_size=4656.

Security, date, and coupon rules:
- Convert fractions such as 4⅜ to coupon_rate=4.375.
- Float, FRN, or SOFR indicates coupon_type=FRN. Set coupon_rate only when a separate
  coupon is explicitly stated. For example, in CDCAUS 6/56 FRN, 6/56 is maturity
  2056-06, not coupon_rate=6.
- Fixed is used only when a fixed coupon is present or clearly implied.
- Normalize MM/DD/YY as YYYY-MM-DD, MM/YY as YYYY-MM, and a year as YYYY. Treat full
  numeric slash dates as month/day/year unless the source explicitly says otherwise.
  MM/DD-YY is also a full date.
- A standalone two-digit token such as 29, 34, 36, 38, or 46 in security position can
  be a maturity year. MTRC 34 €99.05/99.25 means maturity_date=2034 and coupon_rate=null.
  IIFOIN 7.6 29 100.15/100.30 means coupon_rate=7.6 and maturity_date=2029.

Currency rules, in precedence order:
1. Use an explicit currency or symbol on the quote line.
2. Otherwise use a shared paragraph heading such as HKD CREDIT, SCB CNH, AUD trading,
   or $ Japan IG for every compatible row.
3. If there is no conflicting currency evidence, an unmarked international Asia-credit
   price/spread quote may use the desk default currency USD.
Normalize $ to USD, € to EUR, and £ to GBP. Country tags such as {IN}/{AU} and flags do
not determine currency by themselves.

Quote, side, size, and market rules:
- quote_type is price for a price pair, spread for a bp spread pair, offer for an
  offer-only quote/list, bid for a bid-only quote, and yield for a yield-only quote.
- Store price pairs in bid_price/ask_price. For an abbreviated pair such as 100.25/40,
  ask_price=100.40. Preserve bid/ask order for slash pairs.
- Store spread pairs in bid_spread/ask_spread and set spread_bid_ask to their absolute
  difference in bp. A signed single spread keeps its sign. Use surrounding labels and
  header semantics to decide whether a bare pair such as 66/63 is price or spread; mark
  needs_review if the evidence is insufficient.
- Size output numbers use k as their canonical unit, while preserving the displayed
  numeric value. Never multiply by 1,000: 25x10mm => bid_size=25, ask_size=10;
  Buyer 2mm => bid_size=2; Seller 2mm => ask_size=2; ASz(M)=5000 => ask_size=5000;
  ASz(MM)=25 => ask_size=25.
- bond_type=Primary only with explicit primary-market evidence such as launch,
  bookbuilding, initial guidance, or priced new-issue terms. Explicit trading, trading
  more here, updates, or axes means Secondary. A country tag, >> marker, or recently
  issued bond alone does not make a quote Primary. Otherwise use Secondary.

Before returning JSON, silently verify every numeric output against a source token and
the applicable header: maturity is not coupon, yield is not spread, size was not scaled,
shared currency was applied, and Primary has explicit primary-market evidence. Parse
identically formatted rows consistently.
"""


PARAGRAPH_CONTEXT_MAX_LINES = 5


@dataclass
class ExtractionBatch:
    paragraph_id: str
    paragraph: Optional[ParagraphBlock]
    quote_lines: List[QuoteLine]


@dataclass
class BatchExtractionResult:
    records: List[QuoteRecord]
    failures: List[ExtractionFailure]


class OpenAIQuoteExtractor:
    def __init__(
        self,
        model: Optional[str] = None,
        batch_size: int = 8,
        max_workers: int = 1,
        max_retries: int = 2,
        request_timeout: float = 30,
        show_progress: bool = True,
        base_url: Optional[str] = None,
        client: Optional[OpenAI] = None,
        ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or greater")
        self.model = model or os.getenv("OPENAI_MODEL") or MID_MODEL
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.show_progress = show_progress
        self.failures: List[ExtractionFailure] = []
        self.client = client or OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
            max_retries=0,
            timeout=_http_timeout(request_timeout),
        )

    def extract(
        self,
        quote_lines: List[QuoteLine],
        paragraphs: Optional[List[ParagraphBlock]] = None,
    ) -> List[QuoteRecord]:
        self.failures = []
        batches = _paragraph_batches(quote_lines, paragraphs or [], self.batch_size)
        if self.max_workers <= 1 or len(batches) <= 1:
            return self._extract_sequential(batches)

        return self._extract_parallel(batches)

    def _extract_sequential(self, batches: List[ExtractionBatch]) -> List[QuoteRecord]:
        records: List[QuoteRecord] = []
        for batch_index, batch in _progress_batches(batches, self.show_progress):
            started_at = time.time()
            result = self._extract_batch(batch)
            records.extend(result.records)
            self.failures.extend(result.failures)
            _report_batch_done(
                batch_index=batch_index,
                total_batches=len(batches),
                paragraph_id=batch.paragraph_id,
                batch_size=len(batch.quote_lines),
                batch_records=len(result.records),
                batch_failures=len(result.failures),
                total_records=len(records),
                total_failures=len(self.failures),
                elapsed_seconds=time.time() - started_at,
                enabled=self.show_progress,
            )
        return records

    def _extract_parallel(self, batches: List[ExtractionBatch]) -> List[QuoteRecord]:
        results: Dict[int, BatchExtractionResult] = {}
        max_workers = min(self.max_workers, len(batches))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(self._timed_extract_batch, batch): index
                for index, batch in enumerate(batches, start=1)
            }

            for future in _progress_futures(future_to_index, self.show_progress):
                batch_index = future_to_index[future]
                result, elapsed_seconds = future.result()
                results[batch_index] = result
                _report_batch_done(
                    batch_index=batch_index,
                    total_batches=len(batches),
                    paragraph_id=batches[batch_index - 1].paragraph_id,
                    batch_size=len(batches[batch_index - 1].quote_lines),
                    batch_records=len(result.records),
                    batch_failures=len(result.failures),
                    total_records=sum(len(item.records) for item in results.values()),
                    total_failures=sum(len(item.failures) for item in results.values()),
                    elapsed_seconds=elapsed_seconds,
                    enabled=self.show_progress,
                )

        ordered_records: List[QuoteRecord] = []
        for batch_index in sorted(results):
            ordered_records.extend(results[batch_index].records)
            self.failures.extend(results[batch_index].failures)
        return ordered_records

    def _timed_extract_batch(self, batch: ExtractionBatch):
        started_at = time.time()
        return self._extract_batch(batch), time.time() - started_at

    def _extract_batch(self, batch: ExtractionBatch) -> BatchExtractionResult:
        try:
            records = self._call_model(batch)
        except RuntimeError as exc:
            return BatchExtractionResult(
                records=[],
                failures=_failures_from_batch(
                    batch=batch,
                    model=self.model,
                    attempts=self.max_retries + 1,
                    error=exc.__cause__ or exc,
                ),
            )
        return BatchExtractionResult(
            records=records,
            failures=[],
        )

    def _call_model(self, batch: ExtractionBatch) -> List[QuoteRecord]:
        user_content = json.dumps(
            {
                "paragraph_context": _paragraph_payload(batch.paragraph),
                "quote_lines": [
                    {
                        "line_no": line_no,
                        "line_id": quote_line.line_id,
                        "text": quote_line.text,
                    }
                    for line_no, quote_line in enumerate(batch.quote_lines, start=1)
                ],
            },
            ensure_ascii=False,
        )
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
                payload = load_json_object(content)
                return _records_from_payload(payload, batch.quote_lines)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2**attempt, 8))

        raise RuntimeError(f"OpenAI quote extraction failed: {last_error}") from last_error


def _failures_from_batch(
    batch: ExtractionBatch,
    model: str,
    attempts: int,
    error: Exception,
) -> List[ExtractionFailure]:
    return [
        ExtractionFailure(
            line_id=quote_line.line_id,
            paragraph_id=quote_line.paragraph_id,
            raw_line=quote_line.text,
            model=model,
            attempts=attempts,
            error_type=type(error).__name__,
            error_message=str(error),
            quote_time=quote_line.quote_time,
            quote_source=quote_line.quote_source,
            speaker=quote_line.speaker,
            source_date=quote_line.source_date,
        )
        for quote_line in batch.quote_lines
    ]


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


def _paragraph_batches(
    quote_lines: List[QuoteLine],
    paragraphs: List[ParagraphBlock],
    batch_size: int,
) -> List[ExtractionBatch]:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")

    paragraphs_by_id = {paragraph.paragraph_id: paragraph for paragraph in paragraphs}
    grouped_lines: Dict[str, List[QuoteLine]] = {}
    for quote_line in quote_lines:
        grouped_lines.setdefault(quote_line.paragraph_id, []).append(quote_line)

    batches: List[ExtractionBatch] = []
    for paragraph_id, paragraph_lines in grouped_lines.items():
        for line_batch in _chunks(paragraph_lines, batch_size):
            batches.append(
                ExtractionBatch(
                    paragraph_id=paragraph_id,
                    paragraph=paragraphs_by_id.get(paragraph_id),
                    quote_lines=line_batch,
                )
            )
    return batches


def _paragraph_payload(paragraph: Optional[ParagraphBlock]) -> Optional[Dict[str, Any]]:
    if paragraph is None:
        return None
    return {
        "paragraph_id": paragraph.paragraph_id,
        "paragraph_type": paragraph.paragraph_type,
        "text": "\n".join(paragraph.text.split("\n")[:PARAGRAPH_CONTEXT_MAX_LINES]),
        "quote_time": paragraph.quote_time,
        "quote_source": paragraph.quote_source,
        "speaker": paragraph.speaker,
        "source_date": paragraph.source_date,
    }


def _chunks(values: List[QuoteLine], size: int) -> Iterable[List[QuoteLine]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _progress_batches(batches: List[ExtractionBatch], enabled: bool):
    if not enabled:
        return enumerate(batches, start=1)

    try:
        from tqdm import tqdm
    except ImportError:
        return enumerate(batches, start=1)

    return enumerate(tqdm(batches, desc="LLM extract", unit="batch"), start=1)


def _progress_futures(future_to_index, enabled: bool):
    futures = as_completed(future_to_index)
    if not enabled:
        return futures

    try:
        from tqdm import tqdm
    except ImportError:
        return futures

    return tqdm(futures, total=len(future_to_index), desc="LLM extract", unit="batch")


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
    paragraph_id: str,
    batch_size: int,
    batch_records: int,
    batch_failures: int,
    total_records: int,
    total_failures: int,
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
        f"paragraph={paragraph_id}, "
        f"quote_lines={batch_size}, "
        f"quotes={batch_records}, "
        f"failures={batch_failures}, "
        f"total_quotes={total_records}, "
        f"total_failures={total_failures}, "
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
