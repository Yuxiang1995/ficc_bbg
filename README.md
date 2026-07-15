# FICC Bloomberg Chat Quote Parser

This project parses Bloomberg chat/email text into one quote record per bond quote.

PostgreSQL is the default production output. JSON, XLSX, paragraph JSON, and quote-line files remain available as debug outputs.

Splitting is two-step: local code first splits Bloomberg text into raw timestamped messages, then an OpenAI-compatible LLM classifies each raw message and identifies quote line ranges. The extractor LLM then extracts structured fields from those quote lines only.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with OpenAI-compatible API and SQL Server credentials.

Initialize the PostgreSQL quote schema after setting `POSTGRES_PASSWORD`:

```bash
python -m ficc_bbg_parser.pg_schema
```

## Run

The default output mode is `postgres`:

```bash
python -m ficc_bbg_parser.cli \
  /path/to/pasted-text.txt \
  --input-source txt \
  --source-date 2026-06-04 \
  --stage all
```

This writes one transaction to `ficc.ingestion_run`, `chat_email`, `chat_paragraph`, `quote_line`, and `bond_quote`.

Use `--output-mode debug` to skip PostgreSQL and write only:

- `output/quotes.json`
- `output/quotes.xlsx`
- `output/paragraphs.json`
- `output/quote_lines.json`
- `output/quote_lines.txt`

Use `--output-mode both` to write PostgreSQL and all debug files.

## Step-by-step Debugging

Run only the splitter from a debug txt file:

```bash
python -m ficc_bbg_parser.cli tests/email_text.txt --input-source txt --stage split --source-date 2026-06-04 --out-dir output
```

This writes `output/paragraphs.json`, `output/quote_lines.json`, and `output/quote_lines.txt`. The quote-line text file contains one quote statement per line. The splitter shows `LLM classify` progress by raw-message batch.

Run only LLM extraction from previously generated quote lines:

```bash
python -m ficc_bbg_parser.cli \
  --stage extract \
  --quote-lines-input output/quote_lines.json \
  --paragraphs-input output/paragraphs.json \
  --output-mode debug \
  --out-dir output \
  --extract-workers 4 \
  --batch-size 10
```

The extractor groups quote lines by paragraph and includes the first five lines of that paragraph once as auxiliary context in each LLM request. Paragraphs larger than `--batch-size` are split into multiple requests. The extractor shows batch progress by default. Disable it with `--no-progress`.

## Production SQL Server Input

Production mode reads `CONTENT` from SQL Server for one `RECEIVE_DATE` day:

```bash
python -m ficc_bbg_parser.cli \
  --input-source sqlserver \
  --receive-date 2026-06-04 \
  --stage all \
  --output-mode postgres \
  --extract-workers 4
```

SQL Server settings live in `.env`:

```dotenv
DB_SERVER=172.30.10.78
DB_PORT=1433
DB_DATABASE=FICC_BBG_IB_PRD
DB_TABLE=BBG_IB_CHAT_EMAIL_INFO
DB_USERNAME=
DB_PASSWORD=
```

The SQL filter is:

```sql
WHERE RECEIVE_DATE >= @date AND RECEIVE_DATE < DATEADD(day, 1, @date)
```

## PostgreSQL Quote Store

The PostgreSQL schema follows the parser stages:

```text
ingestion_run
  -> chat_email
  -> chat_paragraph
  -> quote_line
  -> bond_quote
```

`bond_security` stores enriched security-master data separately. `bond_quote` keeps typed query columns and the complete extractor result in `raw_payload JSONB`. Partial maturities such as `2031` and `2031-06` are stored using `maturity_text` and `maturity_precision`; `maturity_date` is used only when a full date is known.

Migrations are versioned and safe to run repeatedly. A source email is unique within an ingestion run, while `quote_sequence` allows defensive storage of more than one extracted quote from a malformed source line.

Each CLI write is transactional. If any paragraph, quote line, or quote insert fails, the complete run is rolled back. Successful output prints the PostgreSQL `run_id` and inserted quote count.

Check recent runs and quotes:

```sql
SELECT run_id, source_date, status, quote_count, completed_at
FROM ficc.ingestion_run
ORDER BY run_id DESC
LIMIT 20;

SELECT ticker, maturity_text, bid_price, ask_price,
       bid_spread, ask_spread, quote_time, quote_source
FROM ficc.bond_quote
ORDER BY quote_time DESC
LIMIT 50;
```

PostgreSQL settings live in `.env`:

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DATABASE=ficc_bbg
POSTGRES_SCHEMA=ficc
POSTGRES_USERNAME=ficc_app
POSTGRES_PASSWORD=
POSTGRES_SSLMODE=prefer
```

Useful tuning flags:

- `--batch-size`: maximum quote lines from one paragraph per extractor LLM call
- `--extract-workers`: number of parallel extractor LLM requests
- `--message-batch-max-chars`: target max payload chars per splitter LLM call
- `--model`: extraction model
- `--base-url`: OpenAI-compatible API base URL
- `--max-retries`: retries per LLM batch
- `--split-timeout`: HTTP timeout seconds for the splitter request
- `--extract-timeout`: HTTP timeout seconds for each extractor request
- `--output-mode postgres`: write PostgreSQL only; this is the default
- `--output-mode debug`: write JSON/XLSX and intermediate files only
- `--output-mode both`: write PostgreSQL and debug files

## Parsing Layers

1. Locally split raw Bloomberg chat text into timestamped raw messages using the Bloomberg time/speaker pattern.
2. Send raw message batches to the LLM classifier.
3. The classifier labels each message as `quote`, `axes`, `primary`, `stats`, `commentary`, or `noise`, and returns quote line ranges.
4. Local code slices the original text by line range into `quote_lines`, one bond quote per line.
5. The extractor groups quote lines by paragraph and receives the paragraph's first five lines once as auxiliary context plus the target quote lines.
6. The extractor returns one structured record per quote.
7. Store the complete source chain and typed quote fields in PostgreSQL in one transaction.
8. Optionally export JSON/XLSX and intermediate files in `debug` or `both` mode.

## Notes

- ISIN, issuer full name, rating, currency, and region are only filled when the chat text or context provides them.
- A later security-master enrichment layer should match `ticker + coupon_rate + maturity_date + currency` or ISIN to fill missing static fields.
- Spread quotes store `bid_spread`, `ask_spread`, and `spread_bid_ask` in bp. Price quotes store `bid_price` and `ask_price`.
