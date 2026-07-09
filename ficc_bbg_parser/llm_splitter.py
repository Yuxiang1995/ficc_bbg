import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from openai import OpenAI

from .json_utils import load_json_object
from .models import ParagraphBlock, QuoteLine
from .normalization import clean_text
from .settings import BIG_MODEL, DEFAULT_BASE_URL


MESSAGE_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<speaker>[^:]+?)\s*:\s*(?P<body>.*)$")


SPLIT_SYSTEM_PROMPT = """You classify Bloomberg institutional bond chat/email raw messages.

Return only one valid JSON object. Do not wrap it in markdown. Do not include explanatory text.

The input is already split into raw messages by timestamp. Each message contains global line
numbers in square brackets, e.g. [0007]. Use those line numbers exactly.

Return this JSON shape:
{
  "messages": [
    {
      "message_id": "msg_00001",
      "message_type": "quote" | "axes" | "primary" | "stats" | "commentary" | "noise",
      "quote_line_ranges": [
        {"start_line": 12, "end_line": 12}
      ],
      "quote_source": "speaker/title/source or null",
      "confidence": 0.0
    }
  ]
}

Message type definitions:
- quote: one or more direct bond quote statements, e.g. price, spread, bid, offer, bid/ask.
- axes: axe/list/table of bond quotes or offers. Include one quote_line_range per data row.
- primary: new issue mandate, FTT, IPT, book update, launch/pricing/stat announcement without secondary quote rows.
- stats: orderbook/deal stats, allocation stats, distribution stats, market statistics.
- commentary: market color, credit comments, morning brief, macro commentary.
- noise: chat participants, logistics, URLs, meeting details, greetings, unrelated tables such as CD/deposit runs.

quote_line_ranges rules:
- For quote and axes messages, identify only actual tradable quote rows or quote statements.
- Use one range per bond quote.
- For tables, exclude headers and section labels. Include only data rows that are bond quotes.
- If a quote statement spans multiple physical lines, use a multi-line range, but usually no more than 3 lines.
- For primary, stats, commentary, and noise messages, quote_line_ranges should usually be [].
- Do not invent line numbers or quote text.
- Return every input message exactly once in the messages array.
"""


@dataclass
class RawMessage:
    message_id: str
    message_time: str
    speaker: str
    text: str
    start_line: int
    end_line: int
    source_date: Optional[str] = None

    @property
    def quote_time(self) -> str:
        if self.source_date:
            return f"{self.source_date} {self.message_time}"
        return self.message_time


class OpenAIParagraphSplitter:
    def __init__(
        self,
        model: Optional[str] = None,
        message_batch_max_chars: int = 16000,
        max_retries: int = 1,
        request_timeout: float = 180,
        show_progress: bool = True,
        base_url: Optional[str] = None,
        client: Optional[OpenAI] = None,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL") or BIG_MODEL
        self.message_batch_max_chars = message_batch_max_chars
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.show_progress = show_progress
        self.client = client or OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
            max_retries=0,
            timeout=_http_timeout(request_timeout),
        )

    def split(self, raw_text: str, source_date: Optional[str] = None) -> Tuple[List[ParagraphBlock], List[QuoteLine]]:
        text = clean_text(raw_text)
        lines = text.splitlines()
        raw_messages = _split_raw_messages(lines, source_date=source_date)
        started_at = time.time()
        _report_split_start(lines=lines, raw_messages=raw_messages, enabled=self.show_progress)
        response = self._classify_messages(raw_messages=raw_messages, lines=lines)
        paragraphs, quote_lines = _paragraphs_from_payload(
            payload=response,
            raw_messages=raw_messages,
            lines=lines,
            source_date=source_date,
        )
        _report_split_done(
            paragraphs=len(paragraphs),
            quote_lines=len(quote_lines),
            elapsed_seconds=time.time() - started_at,
            enabled=self.show_progress,
        )
        return paragraphs, quote_lines

    def _classify_messages(self, raw_messages: List[RawMessage], lines: List[str]) -> Dict[str, Any]:
        classified_messages: List[Dict[str, Any]] = []
        batches = _balanced_message_batches(
            raw_messages=raw_messages,
            lines=lines,
            target_chars=self.message_batch_max_chars,
        )

        for batch_index, batch in _progress_batches(batches, self.show_progress):
            started_at = time.time()
            response = self._call_model(raw_messages=batch, lines=lines)
            classified_messages.extend(response.get("messages", []))
            _report_batch_done(
                batch_index=batch_index,
                total_batches=len(batches),
                batch_size=len(batch),
                batch_chars=_batch_weight(batch, lines),
                classified=len(response.get("messages", [])),
                total_classified=len(classified_messages),
                elapsed_seconds=time.time() - started_at,
                enabled=self.show_progress,
            )

        return {"messages": classified_messages}

    def _call_model(self, raw_messages: List[RawMessage], lines: List[str]) -> Dict[str, Any]:
        user_content = json.dumps(
            {
                "messages": [_message_payload(message, lines) for message in raw_messages],
            },
            ensure_ascii=False,
            indent=2,
        )
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                _report_attempt(
                    label="split",
                    attempt=attempt + 1,
                    max_attempts=self.max_retries + 1,
                    timeout_seconds=self.request_timeout,
                    enabled=self.show_progress,
                )
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SPLIT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    extra_body={"enable_thinking": False},
                    max_tokens=8192,
                    temperature=0,
                    timeout=_http_timeout(self.request_timeout),
                )
                content = completion.choices[0].message.content
                if content is None:
                    raise ValueError("OpenAI split response did not include message content")
                return load_json_object(content)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break

        raise RuntimeError(f"OpenAI message classification failed: {last_error}") from last_error


def _split_raw_messages(lines: List[str], source_date: Optional[str]) -> List[RawMessage]:
    raw_messages: List[RawMessage] = []
    current = None
    current_lines: List[str] = []
    current_start_line = 0

    for line_no, raw_line in enumerate(lines, start=1):
        match = MESSAGE_RE.match(raw_line)
        if match:
            if current is not None:
                raw_messages.append(
                    RawMessage(
                        message_id=f"msg_{len(raw_messages) + 1:05d}",
                        message_time=current["time"],
                        speaker=current["speaker"].strip(),
                        text="\n".join(current_lines).strip(),
                        start_line=current_start_line,
                        end_line=line_no - 1,
                        source_date=source_date,
                    )
                )
            current = {
                "time": match.group("time"),
                "speaker": match.group("speaker"),
            }
            current_lines = [match.group("body")]
            current_start_line = line_no
            continue

        if current is not None:
            current_lines.append(raw_line)

    if current is not None:
        raw_messages.append(
            RawMessage(
                message_id=f"msg_{len(raw_messages) + 1:05d}",
                message_time=current["time"],
                speaker=current["speaker"].strip(),
                text="\n".join(current_lines).strip(),
                start_line=current_start_line,
                end_line=len(lines),
                source_date=source_date,
            )
        )

    return raw_messages


def _message_payload(message: RawMessage, lines: List[str]) -> Dict[str, Any]:
    return {
        "message_id": message.message_id,
        "message_time": message.message_time,
        "speaker": message.speaker,
        "start_line": message.start_line,
        "end_line": message.end_line,
        "numbered_text": _numbered_lines(lines, message.start_line, message.end_line),
    }


def _paragraphs_from_payload(
    payload: Dict[str, Any],
    raw_messages: List[RawMessage],
    lines: List[str],
    source_date: Optional[str],
) -> Tuple[List[ParagraphBlock], List[QuoteLine]]:
    paragraphs: List[ParagraphBlock] = []
    quote_lines: List[QuoteLine] = []
    raw_by_id = {message.message_id: message for message in raw_messages}
    classified_by_id = {
        item.get("message_id"): item
        for item in payload.get("messages", [])
        if item.get("message_id") in raw_by_id
    }

    for raw_message in raw_messages:
        item = classified_by_id.get(raw_message.message_id, {})
        message_type = item.get("message_type") or "noise"
        paragraph = ParagraphBlock(
            paragraph_id=raw_message.message_id,
            paragraph_type=message_type,
            text=_slice_lines(lines, raw_message.start_line, raw_message.end_line),
            quote_time=raw_message.quote_time,
            quote_source=item.get("quote_source") or _default_quote_source(raw_message),
            speaker=raw_message.speaker,
            source_date=source_date,
            confidence=float(item.get("confidence") or 0),
        )
        paragraphs.append(paragraph)

        for line_range in item.get("quote_line_ranges") or []:
            quote_bounds = _line_bounds(
                line_range.get("start_line"),
                line_range.get("end_line"),
                total_lines=len(lines),
                container_start=raw_message.start_line,
                container_end=raw_message.end_line,
            )
            if quote_bounds is None:
                continue
            quote_start, quote_end = quote_bounds
            cleaned = " ".join(_slice_lines(lines, quote_start, quote_end).split())
            if cleaned:
                quote_lines.append(
                    QuoteLine(
                        line_id=f"line_{len(quote_lines) + 1:05d}",
                        paragraph_id=paragraph.paragraph_id,
                        text=cleaned,
                        quote_time=paragraph.quote_time,
                        quote_source=paragraph.quote_source,
                        speaker=paragraph.speaker,
                        source_date=source_date,
                    )
                )

    return paragraphs, quote_lines


def _numbered_lines(lines: List[str], start_line: int, end_line: int) -> str:
    width = max(4, len(str(len(lines))))
    return "\n".join(
        f"[{idx:0{width}d}] {lines[idx - 1]}"
        for idx in range(start_line, end_line + 1)
    )


def _line_bounds(
    start_value,
    end_value,
    total_lines: int,
    container_start: int,
    container_end: int,
) -> Optional[Tuple[int, int]]:
    try:
        start_line = int(start_value)
        end_line = int(end_value)
    except (TypeError, ValueError):
        return None
    if start_line < 1 or end_line < start_line or start_line > total_lines:
        return None
    if start_line < container_start or end_line > container_end:
        return None
    return start_line, min(end_line, total_lines)


def _slice_lines(lines: List[str], start_line: int, end_line: int) -> str:
    return "\n".join(lines[start_line - 1 : end_line]).strip()


def _default_quote_source(message: RawMessage) -> str:
    title = ""
    for line in message.text.splitlines():
        title = line.strip().strip("* ").strip()
        if title:
            break
    if title:
        return f"{message.speaker} | {title}"
    return message.speaker


def _balanced_message_batches(
    raw_messages: List[RawMessage],
    lines: List[str],
    target_chars: int,
) -> List[List[RawMessage]]:
    if not raw_messages:
        return []

    target_chars = max(1, target_chars)
    weighted = [(message, _message_weight(message, lines)) for message in raw_messages]
    total_weight = sum(weight for _, weight in weighted)
    batch_count = max(1, (total_weight + target_chars - 1) // target_chars + 1)
    batch_count = min(batch_count, len(raw_messages))

    batches: List[List[RawMessage]] = [[] for _ in range(batch_count)]
    batch_weights = [0 for _ in batches]

    for message, weight in sorted(weighted, key=lambda item: item[1], reverse=True):
        best_index = min(range(len(batches)), key=lambda idx: batch_weights[idx])
        batches[best_index].append(message)
        batch_weights[best_index] += weight

    for batch in batches:
        batch.sort(key=lambda message: message.start_line)

    return [batch for batch in sorted(batches, key=lambda batch: batch[0].start_line if batch else 0) if batch]


def _message_weight(message: RawMessage, lines: List[str]) -> int:
    return len(json.dumps(_message_payload(message, lines), ensure_ascii=False))


def _batch_weight(batch: List[RawMessage], lines: List[str]) -> int:
    return len(json.dumps({"messages": [_message_payload(message, lines) for message in batch]}, ensure_ascii=False))


def _progress_batches(batches: List[List[RawMessage]], enabled: bool):
    if not enabled:
        return enumerate(batches, start=1)

    try:
        from tqdm import tqdm
    except ImportError:
        return enumerate(batches, start=1)

    return enumerate(tqdm(batches, desc="LLM classify", unit="batch"), start=1)


def _report_split_start(lines: List[str], raw_messages: List[RawMessage], enabled: bool) -> None:
    if not enabled:
        return

    print(
        "split start: "
        f"chars={sum(len(line) for line in lines)}, "
        f"lines={len(lines)}, "
        f"raw_messages={len(raw_messages)}",
        file=sys.stderr,
    )


def _report_batch_done(
    batch_index: int,
    total_batches: int,
    batch_size: int,
    batch_chars: int,
    classified: int,
    total_classified: int,
    elapsed_seconds: float,
    enabled: bool,
) -> None:
    if not enabled:
        return

    print(
        "classify "
        f"{batch_index}/{total_batches}: "
        f"raw_messages={batch_size}, "
        f"chars={batch_chars}, "
        f"classified={classified}, "
        f"total_classified={total_classified}, "
        f"elapsed={elapsed_seconds:.1f}s",
        file=sys.stderr,
    )


def _report_split_done(
    paragraphs: int,
    quote_lines: int,
    elapsed_seconds: float,
    enabled: bool,
) -> None:
    if not enabled:
        return

    print(
        "split done: "
        f"paragraphs={paragraphs}, "
        f"quote_lines={quote_lines}, "
        f"elapsed={elapsed_seconds:.1f}s",
        file=sys.stderr,
    )


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


def _http_timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=seconds,
        connect=min(10.0, seconds),
        read=seconds,
        write=min(30.0, seconds),
        pool=min(10.0, seconds),
    )
