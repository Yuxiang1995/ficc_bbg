import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from . import settings
from .models import ExtractionFailure, ParagraphBlock, QuoteLine, QuoteRecord


HK_TZ = ZoneInfo("Asia/Hong_Kong")


@dataclass
class StoreResult:
    run_id: int
    email_count: int
    paragraph_count: int
    quote_line_count: int
    quote_count: int
    failure_count: int


class PostgresQuoteStore:
    def __init__(self, connection_factory: Optional[Callable[..., Any]] = None) -> None:
        self.connection_factory = connection_factory

    def save_run(
        self,
        source_mode: str,
        source_date: Optional[str],
        emails: Iterable[Dict[str, Any]],
        paragraphs: List[ParagraphBlock],
        quote_lines: List[QuoteLine],
        records: List[QuoteRecord],
        failures: Optional[List[ExtractionFailure]] = None,
        splitter_model: Optional[str] = None,
        extractor_model: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StoreResult:
        psycopg, Jsonb = _import_psycopg()
        email_rows = list(emails)
        failure_rows = failures or []
        effective_paragraphs = _with_missing_paragraphs(paragraphs, quote_lines)

        connect = self.connection_factory or psycopg.connect
        with connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            dbname=settings.POSTGRES_DATABASE,
            user=settings.POSTGRES_USERNAME,
            password=_required("POSTGRES_PASSWORD", settings.POSTGRES_PASSWORD),
            sslmode=settings.POSTGRES_SSLMODE,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    psycopg.sql.SQL("SET LOCAL search_path TO {}, public").format(
                        psycopg.sql.Identifier(settings.POSTGRES_SCHEMA)
                    )
                )
                run_id = _insert_run(
                    cursor=cursor,
                    Jsonb=Jsonb,
                    source_mode=source_mode,
                    source_date=source_date,
                    splitter_model=splitter_model,
                    extractor_model=extractor_model,
                    metadata=metadata or {},
                )
                email_ids = _insert_emails(cursor, Jsonb, run_id, email_rows)
                paragraph_ids = _insert_paragraphs(
                    cursor,
                    run_id,
                    effective_paragraphs,
                    email_ids,
                )
                quote_line_ids = _insert_quote_lines(
                    cursor,
                    run_id,
                    quote_lines,
                    paragraph_ids,
                )
                quote_count = _insert_quotes(
                    cursor,
                    Jsonb,
                    run_id,
                    source_mode,
                    source_date,
                    quote_lines,
                    quote_line_ids,
                    records,
                )
                failure_count = _insert_failures(
                    cursor,
                    Jsonb,
                    run_id,
                    quote_line_ids,
                    failure_rows,
                )
                cursor.execute(
                    """
                    UPDATE ingestion_run
                    SET status = 'completed',
                        raw_record_count = %s,
                        paragraph_count = %s,
                        quote_line_count = %s,
                        quote_count = %s,
                        extraction_failure_count = %s,
                        completed_at = now()
                    WHERE run_id = %s
                    """,
                    (
                        len(email_rows),
                        len(effective_paragraphs),
                        len(quote_lines),
                        quote_count,
                        failure_count,
                        run_id,
                    ),
                )

        return StoreResult(
            run_id=run_id,
            email_count=len(email_rows),
            paragraph_count=len(effective_paragraphs),
            quote_line_count=len(quote_lines),
            quote_count=quote_count,
            failure_count=failure_count,
        )


def _insert_run(
    cursor,
    Jsonb,
    source_mode: str,
    source_date: Optional[str],
    splitter_model: Optional[str],
    extractor_model: Optional[str],
    metadata: Dict[str, Any],
) -> int:
    cursor.execute(
        """
        INSERT INTO ingestion_run (
            source_mode, source_date, splitter_model, extractor_model, metadata
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING run_id
        """,
        (
            source_mode,
            _parse_date(source_date),
            splitter_model,
            extractor_model,
            Jsonb(metadata),
        ),
    )
    return int(cursor.fetchone()[0])


def _insert_emails(cursor, Jsonb, run_id: int, emails: List[Dict[str, Any]]) -> List[int]:
    email_ids = []
    for email in emails:
        content = str(email.get("content") or "")
        subject = email.get("subject")
        receive_time = _parse_datetime(email.get("receive_date"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source_system = str(email.get("source_system") or "sqlserver")
        source_record_key = email.get("source_record_key") or _source_record_key(
            source_system,
            subject,
            receive_time,
            content_hash,
        )
        cursor.execute(
            """
            INSERT INTO chat_email (
                run_id, source_system, source_record_key, subject, content,
                receive_time, content_hash, raw_payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING email_id
            """,
            (
                run_id,
                source_system,
                source_record_key,
                subject,
                content,
                receive_time,
                content_hash,
                Jsonb(
                    {
                        "subject": subject,
                        "receive_date": receive_time.isoformat() if receive_time else None,
                    }
                ),
            ),
        )
        email_ids.append(int(cursor.fetchone()[0]))
    return email_ids


def _insert_paragraphs(
    cursor,
    run_id: int,
    paragraphs: List[ParagraphBlock],
    email_ids: List[int],
) -> Dict[str, int]:
    paragraph_ids = {}
    email_id = email_ids[0] if len(email_ids) == 1 else None
    for paragraph in paragraphs:
        cursor.execute(
            """
            INSERT INTO chat_paragraph (
                run_id, email_id, paragraph_id, paragraph_type, text, quote_time,
                quote_source, speaker, source_date, confidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING paragraph_pk
            """,
            (
                run_id,
                email_id,
                paragraph.paragraph_id,
                paragraph.paragraph_type,
                paragraph.text,
                _parse_datetime(paragraph.quote_time, paragraph.source_date),
                paragraph.quote_source,
                paragraph.speaker,
                _parse_date(paragraph.source_date),
                paragraph.confidence,
            ),
        )
        paragraph_ids[paragraph.paragraph_id] = int(cursor.fetchone()[0])
    return paragraph_ids


def _insert_quote_lines(
    cursor,
    run_id: int,
    quote_lines: List[QuoteLine],
    paragraph_ids: Dict[str, int],
) -> Dict[str, int]:
    quote_line_ids = {}
    for quote_line in quote_lines:
        paragraph_pk = paragraph_ids.get(quote_line.paragraph_id)
        if paragraph_pk is None:
            raise ValueError(f"Missing paragraph for quote line {quote_line.line_id}")
        cursor.execute(
            """
            INSERT INTO quote_line (
                run_id, paragraph_pk, line_id, source_line_no, text,
                quote_time, quote_source, speaker, source_date
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING quote_line_pk
            """,
            (
                run_id,
                paragraph_pk,
                quote_line.line_id,
                _line_number(quote_line.line_id),
                quote_line.text,
                _parse_datetime(quote_line.quote_time, quote_line.source_date),
                quote_line.quote_source,
                quote_line.speaker,
                _parse_date(quote_line.source_date),
            ),
        )
        quote_line_ids[quote_line.line_id] = int(cursor.fetchone()[0])
    return quote_line_ids


def _insert_quotes(
    cursor,
    Jsonb,
    run_id: int,
    source_mode: str,
    source_date: Optional[str],
    quote_lines: List[QuoteLine],
    quote_line_ids: Dict[str, int],
    records: List[QuoteRecord],
) -> int:
    lines_by_number = {
        line_number: quote_line
        for quote_line in quote_lines
        for line_number in [_line_number(quote_line.line_id)]
        if line_number is not None
    }
    lines_by_content = {
        (quote_line.paragraph_id, quote_line.text): quote_line
        for quote_line in quote_lines
    }
    sequences: Dict[str, int] = {}
    inserted_count = 0

    for record in records:
        quote_line = lines_by_number.get(record.line_no)
        if quote_line is None:
            quote_line = lines_by_content.get((record.message_id, record.raw_line))
        if quote_line is None:
            raise ValueError(
                f"Cannot match quote record to quote line: line_no={record.line_no}, "
                f"message_id={record.message_id}"
            )

        quote_line_pk = quote_line_ids[quote_line.line_id]
        quote_sequence = sequences.get(quote_line.line_id, 0) + 1
        sequences[quote_line.line_id] = quote_sequence
        maturity = _parse_maturity(record.maturity_date)
        quote_time = _parse_datetime(record.quote_time, source_date)
        raw_line = record.raw_line or quote_line.text
        dedupe_key = _quote_dedupe_key(
            source_mode=source_mode,
            source_date=source_date,
            quote_time=quote_time,
            quote_source=record.quote_source,
            message_id=record.message_id,
            line_no=record.line_no,
            raw_line=raw_line,
            quote_sequence=quote_sequence,
        )
        spread_unit = None
        if record.spread_bid_ask is not None:
            spread_unit = "price_point" if record.quote_type == "price" else "bp"

        cursor.execute(
            """
            INSERT INTO bond_quote (
                run_id, quote_line_pk, quote_sequence, dedupe_key,
                ticker, isin, issuer, currency,
                maturity_text, maturity_date, maturity_precision,
                maturity_year, maturity_month, maturity_day,
                coupon_type, coupon_rate, rating_raw,
                bid_price, ask_price, bid_spread, ask_spread,
                spread_bid_ask, spread_unit, bid_size, ask_size,
                quote_time, quote_source, quote_type, bond_type, region, remark,
                message_id, message_type, source_line_no, raw_line,
                parser_name, parse_status, confidence, raw_payload
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (dedupe_key) DO NOTHING
            """,
            (
                run_id,
                quote_line_pk,
                quote_sequence,
                dedupe_key,
                record.ticker,
                record.isin,
                record.issuer,
                record.currency,
                maturity["text"],
                maturity["date"],
                maturity["precision"],
                maturity["year"],
                maturity["month"],
                maturity["day"],
                record.coupon_type,
                record.coupon_rate,
                record.rating,
                record.bid_price,
                record.ask_price,
                record.bid_spread,
                record.ask_spread,
                record.spread_bid_ask,
                spread_unit,
                record.bid_size,
                record.ask_size,
                quote_time,
                record.quote_source,
                record.quote_type,
                record.bond_type,
                record.region,
                record.remark,
                record.message_id,
                record.message_type,
                record.line_no,
                raw_line,
                record.parser_name,
                record.parse_status,
                record.confidence,
                Jsonb(record.to_dict()),
            ),
        )
        inserted_count += cursor.rowcount

    return inserted_count


def _insert_failures(
    cursor,
    Jsonb,
    run_id: int,
    quote_line_ids: Dict[str, int],
    failures: List[ExtractionFailure],
) -> int:
    inserted_count = 0
    for failure in failures:
        quote_line_pk = quote_line_ids.get(failure.line_id)
        if quote_line_pk is None:
            raise ValueError(f"Cannot match extraction failure to quote line {failure.line_id}")
        cursor.execute(
            """
            INSERT INTO quote_extraction_failure (
                run_id, quote_line_pk, model, attempts, error_type,
                error_message, failure_payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, quote_line_pk) DO UPDATE
            SET model = EXCLUDED.model,
                attempts = EXCLUDED.attempts,
                error_type = EXCLUDED.error_type,
                error_message = EXCLUDED.error_message,
                failure_payload = EXCLUDED.failure_payload,
                created_at = now()
            """,
            (
                run_id,
                quote_line_pk,
                failure.model,
                failure.attempts,
                failure.error_type,
                failure.error_message,
                Jsonb(failure.to_dict()),
            ),
        )
        inserted_count += cursor.rowcount
    return inserted_count


def _with_missing_paragraphs(
    paragraphs: List[ParagraphBlock],
    quote_lines: List[QuoteLine],
) -> List[ParagraphBlock]:
    result = list(paragraphs)
    known_ids = {paragraph.paragraph_id for paragraph in paragraphs}
    missing_lines: Dict[str, List[QuoteLine]] = {}
    for quote_line in quote_lines:
        if quote_line.paragraph_id not in known_ids:
            missing_lines.setdefault(quote_line.paragraph_id, []).append(quote_line)

    for paragraph_id, lines in missing_lines.items():
        first = lines[0]
        result.append(
            ParagraphBlock(
                paragraph_id=paragraph_id,
                paragraph_type="quote",
                text="\n".join(line.text for line in lines),
                quote_time=first.quote_time,
                quote_source=first.quote_source,
                speaker=first.speaker,
                source_date=first.source_date,
            )
        )
    return result


def _parse_maturity(value: Optional[str]) -> Dict[str, Any]:
    result = {
        "text": value,
        "date": None,
        "precision": None,
        "year": None,
        "month": None,
        "day": None,
    }
    if not value:
        return result

    parts = value.split("-")
    try:
        if len(parts) == 1 and len(parts[0]) == 4:
            year = int(parts[0])
            date(year, 1, 1)
            result.update(precision="year", year=year)
        elif len(parts) == 2:
            year = int(parts[0])
            month = int(parts[1])
            date(year, month, 1)
            result.update(precision="month", year=year, month=month)
        elif len(parts) == 3:
            full_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
            result.update(
                precision="day",
                date=full_date,
                year=full_date.year,
                month=full_date.month,
                day=full_date.day,
            )
    except ValueError:
        return {
            "text": value,
            "date": None,
            "precision": None,
            "year": None,
            "month": None,
            "day": None,
        }
    return result


def _parse_datetime(value: Any, source_date: Optional[str] = None) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if len(text) == 8 and text.count(":") == 2 and source_date:
            text = f"{source_date} {text}"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HK_TZ)
    return parsed


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _line_number(line_id: str) -> Optional[int]:
    try:
        return int(line_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _source_record_key(
    source_system: str,
    subject: Any,
    receive_time: Optional[datetime],
    content_hash: str,
) -> str:
    raw = "|".join(
        [
            source_system,
            str(subject or ""),
            receive_time.isoformat() if receive_time else "",
            content_hash,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quote_dedupe_key(
    source_mode: str,
    source_date: Optional[str],
    quote_time: Optional[datetime],
    quote_source: Optional[str],
    message_id: Optional[str],
    line_no: Optional[int],
    raw_line: str,
    quote_sequence: int,
) -> str:
    raw = "|".join(
        [
            source_mode,
            str(source_date or ""),
            quote_time.isoformat() if quote_time else "",
            str(quote_source or ""),
            str(message_id or ""),
            str(line_no or ""),
            raw_line,
            str(quote_sequence),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _required(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"Missing required PostgreSQL env var: {name}")
    return value


def _import_psycopg():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise ImportError("psycopg is required for PostgreSQL. Install requirements.txt.") from exc
    return psycopg, Jsonb
