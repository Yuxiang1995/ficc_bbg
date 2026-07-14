# FICC Bloomberg Chat Quote Parser

This project parses Bloomberg chat/email text into one quote record per bond quote.

Current output is for review and debugging only: JSON, XLSX, paragraph JSON, and quote-line files. Database loading can be added after LLM extraction and static-data enrichment are stable.

Splitting is two-step: local code first splits Bloomberg text into raw timestamped messages, then an OpenAI-compatible LLM classifies each raw message and identifies quote line ranges. The extractor LLM then extracts structured fields from those quote lines only.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with OpenAI-compatible API and SQL Server credentials.

## Run

```bash
python -m ficc_bbg_parser.cli /path/to/pasted-text.txt --input-source txt --source-date 2026-06-04 --out-dir output
```

Outputs:

- `output/quotes.json`
- `output/quotes.xlsx`
- `output/paragraphs.json`
- `output/quote_lines.json`
- `output/quote_lines.txt`

## Step-by-step Debugging

Run only the splitter from a debug txt file:

```bash
python -m ficc_bbg_parser.cli tests/email_text.txt --input-source txt --stage split --source-date 2026-06-04 --out-dir output
```

This writes `output/paragraphs.json`, `output/quote_lines.json`, and `output/quote_lines.txt`. The quote-line text file contains one quote statement per line. The splitter shows `LLM classify` progress by raw-message batch.

Run only LLM extraction from previously generated quote lines:

```bash
python -m ficc_bbg_parser.cli --stage extract --quote-lines-input output/quote_lines.json --paragraphs-input output/paragraphs.json --out-dir output --extract-workers 4 --batch-size 10
```

The extractor groups quote lines by paragraph and includes the first five lines of that paragraph once as auxiliary context in each LLM request. Paragraphs larger than `--batch-size` are split into multiple requests. The extractor shows batch progress by default. Disable it with `--no-progress`.

## Production SQL Server Input

Production mode reads `CONTENT` from SQL Server for one `RECEIVE_DATE` day:

```bash
python -m ficc_bbg_parser.cli \
  --input-source sqlserver \
  --receive-date 2026-06-04 \
  --stage all \
  --out-dir output
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

Useful tuning flags:

- `--batch-size`: maximum quote lines from one paragraph per extractor LLM call
- `--extract-workers`: number of parallel extractor LLM requests
- `--message-batch-max-chars`: target max payload chars per splitter LLM call
- `--model`: extraction model
- `--base-url`: OpenAI-compatible API base URL
- `--max-retries`: retries per LLM batch
- `--split-timeout`: HTTP timeout seconds for the splitter request
- `--extract-timeout`: HTTP timeout seconds for each extractor request

## Parsing Layers

1. Locally split raw Bloomberg chat text into timestamped raw messages using the Bloomberg time/speaker pattern.
2. Send raw message batches to the LLM classifier.
3. The classifier labels each message as `quote`, `axes`, `primary`, `stats`, `commentary`, or `noise`, and returns quote line ranges.
4. Local code slices the original text by line range into `quote_lines`, one bond quote per line.
5. The extractor groups quote lines by paragraph and receives the paragraph's first five lines once as auxiliary context plus the target quote lines.
6. The extractor returns one structured record per quote.
7. Export requested DB columns plus debug fields.

## Notes

- ISIN, issuer full name, rating, currency, and region are only filled when the chat text or context provides them.
- A later security-master enrichment layer should match `ticker + coupon_rate + maturity_date + currency` or ISIN to fill missing static fields.
- Spread quotes store `bid_spread`, `ask_spread`, and `spread_bid_ask` in bp. Price quotes store `bid_price` and `ask_price`.
