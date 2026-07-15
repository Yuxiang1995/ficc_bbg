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

Return only one valid JSON object. Do not wrap it in markdown. Do not include explanatory text.

The user message is a JSON object with:
- paragraph_context: up to the first five lines of the shared source paragraph,
  or null when unavailable.
- quote_lines: the quote statements that must be extracted.

quote_lines are the only extraction targets and the primary source of truth.
paragraph_context is auxiliary information only. You may use it to resolve shared
information explicitly present in the paragraph, such as a heading, currency, quote
side, unit, or Primary/Secondary context that is omitted from an individual quote line.
Never create a quote from paragraph_context, and never return a quote that is not tied
to one of the supplied quote_lines. If paragraph_context conflicts with a quote line,
the quote line wins. Do not copy bond-specific values from one quote line to another.

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

Important extraction rules:
- Preserve source values. Do not invent ISIN, issuer full name, rating, currency, or region.
- Use null when a field is unknown or not explicitly implied by the text.
- line_no must equal the 1-based line_no supplied with the quote line in the current batch.
- message_id, quote_time, and quote_source may be null; local post-processing will fill them.
- bond_type is Primary for new issue/primary market lines, otherwise Secondary.
- quote_type is price for price pairs, spread for spread pairs in bp, offer for offer-only axes/lists, bid for bid-only, yield for yield-only.
- Price quotes use bid_price and ask_price.
- For spread pairs, spread_bid_ask is the absolute bid/ask spread difference in bp.
- For size pairs like 25x10mm, bid_size is 25k and ask_size is 10k.
- For abbreviated price pairs like 100.25/40, ask_price is 100.40.
- Convert fractions like 4⅜ to decimal coupon_rate 4.375.
- coupon_type is FRN for Float/FRN/SOFR floating-rate bonds; Fixed for fixed coupon bonds.
- Normalize maturity_date to YYYY-MM-DD when day is present, YYYY-MM when only month/year is present, or YYYY when only year is present.
- raw_line should exactly equal the text of the quote_line that produced the quote.
- confidence is 0 to 1. Use needs_review when a quote is real but key fields are ambiguous.
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
