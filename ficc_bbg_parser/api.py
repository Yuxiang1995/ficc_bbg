from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import settings
from .eval_store import EvaluationDatasetStore


app = FastAPI(
    title="FICC Bond Quote API",
    description="Query API for bond quotes and local evaluation annotation.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "PUT"],
    allow_headers=["*"],
)


QUOTE_SORT_COLUMNS = {
    "quote_time": "q.quote_time",
    "ticker": "q.ticker",
    "maturity_date": "q.maturity_date",
    "coupon_rate": "q.coupon_rate",
    "bid_price": "q.bid_price",
    "ask_price": "q.ask_price",
    "bid_spread": "q.bid_spread",
    "ask_spread": "q.ask_spread",
    "confidence": "q.confidence",
    "created_at": "q.created_at",
}
FAILURE_SORT_COLUMNS = {
    "created_at": "f.created_at",
    "attempts": "f.attempts",
    "model": "f.model",
    "error_type": "f.error_type",
}


@app.get("/api/health")
def health() -> Dict[str, Any]:
    with _cursor() as cursor:
        cursor.execute("SELECT current_database(), now()")
        database, server_time = cursor.fetchone()
    return {"status": "ok", "database": database, "server_time": server_time}


@app.get("/api/summary")
def summary() -> Dict[str, Any]:
    with _cursor() as cursor:
        cursor.execute(
            """
            SELECT
                count(*) AS total_quotes,
                count(*) FILTER (WHERE parse_status = 'needs_review') AS review_quotes,
                count(*) FILTER (WHERE bid_price IS NOT NULL OR bid_spread IS NOT NULL)
                    AS quotes_with_bid,
                count(*) FILTER (WHERE ask_price IS NOT NULL OR ask_spread IS NOT NULL)
                    AS quotes_with_ask,
                max(quote_time) AS latest_quote_time
            FROM bond_quote
            """
        )
        quote_summary = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM quote_extraction_failure")
        failure_count = cursor.fetchone()[0]

    return {
        "total_quotes": quote_summary[0],
        "review_quotes": quote_summary[1],
        "quotes_with_bid": quote_summary[2],
        "quotes_with_ask": quote_summary[3],
        "latest_quote_time": quote_summary[4],
        "extraction_failures": failure_count,
    }


@app.get("/api/options")
def options() -> Dict[str, List[str]]:
    fields = {
        "currencies": "currency",
        "quote_sources": "quote_source",
        "quote_types": "quote_type",
        "bond_types": "bond_type",
        "parse_statuses": "parse_status",
        "regions": "region",
    }
    result: Dict[str, List[str]] = {}
    psycopg = _import_psycopg()
    with _cursor() as cursor:
        for response_key, column in fields.items():
            query = psycopg.sql.SQL(
                "SELECT DISTINCT {column} FROM bond_quote "
                "WHERE {column} IS NOT NULL AND {column} <> '' "
                "ORDER BY {column} LIMIT 200"
            ).format(column=psycopg.sql.Identifier(column))
            cursor.execute(query)
            result[response_key] = [row[0] for row in cursor.fetchall()]
    return result


@app.get("/api/bond-quotes")
def list_bond_quotes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: Optional[str] = None,
    ticker: Optional[str] = None,
    isin: Optional[str] = None,
    currency: Optional[str] = None,
    quote_type: Optional[str] = None,
    bond_type: Optional[str] = None,
    parse_status: Optional[str] = None,
    quote_source: Optional[str] = None,
    region: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    has_bid: Optional[bool] = None,
    has_ask: Optional[bool] = None,
    sort_field: str = "quote_time",
    sort_order: str = "descend",
) -> Dict[str, Any]:
    conditions, params = _quote_filters(
        q=q,
        ticker=ticker,
        isin=isin,
        currency=currency,
        quote_type=quote_type,
        bond_type=bond_type,
        parse_status=parse_status,
        quote_source=quote_source,
        region=region,
        date_from=date_from,
        date_to=date_to,
        min_confidence=min_confidence,
        has_bid=has_bid,
        has_ask=has_ask,
    )
    where_sql = " AND ".join(conditions) if conditions else "TRUE"
    sort_sql = QUOTE_SORT_COLUMNS.get(sort_field, QUOTE_SORT_COLUMNS["quote_time"])
    direction = "ASC" if sort_order == "ascend" else "DESC"
    offset = (page - 1) * page_size

    with _dict_cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS total FROM bond_quote q WHERE " + where_sql,
            params,
        )
        total = cursor.fetchone()["total"]
        cursor.execute(
            """
            SELECT
                q.quote_id, q.run_id, q.ticker, q.isin, q.issuer, q.currency,
                q.maturity_text, q.maturity_date, q.coupon_type, q.coupon_rate,
                q.rating_raw, q.rating_moodys, q.rating_sp, q.rating_fitch,
                q.bid_price, q.ask_price, q.bid_spread, q.ask_spread,
                q.spread_bid_ask, q.spread_unit, q.bid_size, q.ask_size,
                q.size_unit, q.quote_time, q.quote_source, q.quote_type,
                q.bond_type, q.region, q.remark, q.parse_status, q.confidence,
                q.raw_line, q.created_at
            FROM bond_quote q
            WHERE """
            + where_sql
            + " ORDER BY "
            + sort_sql
            + " "
            + direction
            + " NULLS LAST, q.quote_id DESC LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        rows = cursor.fetchall()

    return {"data": rows, "total": total, "page": page, "page_size": page_size}


@app.get("/api/bond-quotes/{quote_id}")
def get_bond_quote(quote_id: int) -> Dict[str, Any]:
    with _dict_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                q.*, l.line_id, l.text AS quote_line_text, l.speaker,
                l.source_date, p.paragraph_id, p.paragraph_type,
                p.text AS paragraph_text, r.source_mode, r.source_date AS run_source_date,
                r.splitter_model, r.extractor_model
            FROM bond_quote q
            JOIN quote_line l ON l.quote_line_pk = q.quote_line_pk
            JOIN chat_paragraph p ON p.paragraph_pk = l.paragraph_pk
            JOIN ingestion_run r ON r.run_id = q.run_id
            WHERE q.quote_id = %s
            """,
            (quote_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return row


@app.get("/api/extraction-failures")
def list_extraction_failures(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: Optional[str] = None,
    model: Optional[str] = None,
    error_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_field: str = "created_at",
    sort_order: str = "descend",
) -> Dict[str, Any]:
    conditions: List[str] = []
    params: List[Any] = []
    _add_text_filter(conditions, params, "f.model", model, exact=True)
    _add_text_filter(conditions, params, "f.error_type", error_type, exact=True)
    if q:
        conditions.append(
            "(l.text ILIKE %s OR l.line_id ILIKE %s OR f.error_message ILIKE %s)"
        )
        pattern = "%{}%".format(q.strip())
        params.extend([pattern, pattern, pattern])
    if date_from:
        conditions.append("f.created_at >= %s::date")
        params.append(date_from)
    if date_to:
        conditions.append("f.created_at < (%s::date + INTERVAL '1 day')")
        params.append(date_to)

    where_sql = " AND ".join(conditions) if conditions else "TRUE"
    sort_sql = FAILURE_SORT_COLUMNS.get(sort_field, FAILURE_SORT_COLUMNS["created_at"])
    direction = "ASC" if sort_order == "ascend" else "DESC"
    offset = (page - 1) * page_size

    with _dict_cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) AS total
            FROM quote_extraction_failure f
            JOIN quote_line l ON l.quote_line_pk = f.quote_line_pk
            WHERE """
            + where_sql,
            params,
        )
        total = cursor.fetchone()["total"]
        cursor.execute(
            """
            SELECT
                f.failure_id, f.run_id, f.model, f.attempts, f.error_type,
                f.error_message, f.failure_payload, f.created_at,
                l.line_id, l.text AS quote_line, l.quote_time, l.quote_source,
                l.speaker, l.source_date, p.paragraph_id, p.paragraph_type,
                p.text AS paragraph_text
            FROM quote_extraction_failure f
            JOIN quote_line l ON l.quote_line_pk = f.quote_line_pk
            JOIN chat_paragraph p ON p.paragraph_pk = l.paragraph_pk
            WHERE """
            + where_sql
            + " ORDER BY "
            + sort_sql
            + " "
            + direction
            + " NULLS LAST, f.failure_id DESC LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        rows = cursor.fetchall()

    return {"data": rows, "total": total, "page": page, "page_size": page_size}


@app.get("/api/evaluation")
def get_evaluation_dataset() -> Dict[str, Any]:
    try:
        return _evaluation_store().load()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/evaluation/samples/{sample_id}/annotation")
def save_evaluation_annotation(
    sample_id: str,
    annotation: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    try:
        return _evaluation_store().save_annotation(sample_id, annotation)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/evaluation/download")
def download_evaluation_dataset() -> FileResponse:
    try:
        active_path = _evaluation_store().active_path()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(
        path=str(active_path),
        media_type="application/json",
        filename="eval_annotated.json",
    )


def _quote_filters(
    q: Optional[str],
    ticker: Optional[str],
    isin: Optional[str],
    currency: Optional[str],
    quote_type: Optional[str],
    bond_type: Optional[str],
    parse_status: Optional[str],
    quote_source: Optional[str],
    region: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    min_confidence: Optional[float],
    has_bid: Optional[bool],
    has_ask: Optional[bool],
) -> Tuple[List[str], List[Any]]:
    conditions: List[str] = []
    params: List[Any] = []
    if q:
        conditions.append(
            "(q.ticker ILIKE %s OR q.isin ILIKE %s OR q.issuer ILIKE %s "
            "OR q.raw_line ILIKE %s)"
        )
        pattern = "%{}%".format(q.strip())
        params.extend([pattern, pattern, pattern, pattern])
    _add_text_filter(conditions, params, "q.ticker", ticker)
    _add_text_filter(conditions, params, "q.isin", isin)
    _add_text_filter(conditions, params, "q.currency", currency, exact=True)
    _add_text_filter(conditions, params, "q.quote_type", quote_type, exact=True)
    _add_text_filter(conditions, params, "q.bond_type", bond_type, exact=True)
    _add_text_filter(conditions, params, "q.parse_status", parse_status, exact=True)
    _add_text_filter(conditions, params, "q.quote_source", quote_source, exact=True)
    _add_text_filter(conditions, params, "q.region", region, exact=True)
    if date_from:
        conditions.append("q.quote_time >= %s::date")
        params.append(date_from)
    if date_to:
        conditions.append("q.quote_time < (%s::date + INTERVAL '1 day')")
        params.append(date_to)
    if min_confidence is not None:
        conditions.append("q.confidence >= %s")
        params.append(min_confidence)
    if has_bid is not None:
        operator = "IS NOT NULL" if has_bid else "IS NULL"
        joiner = " OR " if has_bid else " AND "
        conditions.append(
            "(q.bid_price {}{}q.bid_spread {})".format(operator, joiner, operator)
        )
    if has_ask is not None:
        operator = "IS NOT NULL" if has_ask else "IS NULL"
        joiner = " OR " if has_ask else " AND "
        conditions.append(
            "(q.ask_price {}{}q.ask_spread {})".format(operator, joiner, operator)
        )
    return conditions, params


def _add_text_filter(
    conditions: List[str],
    params: List[Any],
    column: str,
    value: Optional[str],
    exact: bool = False,
) -> None:
    if not value:
        return
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        return
    if exact:
        conditions.append("{} = ANY(%s)".format(column))
        params.append(values)
    else:
        conditions.append(
            "(" + " OR ".join("{} ILIKE %s".format(column) for _ in values) + ")"
        )
        params.extend("%{}%".format(item) for item in values)


@contextmanager
def _cursor() -> Iterator[Any]:
    psycopg = _import_psycopg()
    with _connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                psycopg.sql.SQL("SET search_path TO {}, public").format(
                    psycopg.sql.Identifier(settings.POSTGRES_SCHEMA)
                )
            )
            yield cursor


@contextmanager
def _dict_cursor() -> Iterator[Any]:
    psycopg = _import_psycopg()
    with _connection() as connection:
        with connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                psycopg.sql.SQL("SET search_path TO {}, public").format(
                    psycopg.sql.Identifier(settings.POSTGRES_SCHEMA)
                )
            )
            yield cursor


@contextmanager
def _connection() -> Iterator[Any]:
    psycopg = _import_psycopg()
    if not settings.POSTGRES_PASSWORD:
        raise RuntimeError("Missing required PostgreSQL env var: POSTGRES_PASSWORD")
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DATABASE,
        user=settings.POSTGRES_USERNAME,
        password=settings.POSTGRES_PASSWORD,
        sslmode=settings.POSTGRES_SSLMODE,
    ) as connection:
        yield connection


def _import_psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise ImportError("psycopg is required. Install requirements.txt.") from exc
    return psycopg


def _evaluation_store() -> EvaluationDatasetStore:
    return EvaluationDatasetStore(
        input_path=Path(settings.EVAL_DATASET_PATH),
        output_path=Path(settings.EVAL_ANNOTATED_PATH),
    )


FRONT_DIST = Path(__file__).resolve().parents[1] / "front" / "dist"
if FRONT_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONT_DIST), html=True), name="frontend")
