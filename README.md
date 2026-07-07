# FICC Bloomberg Chat Quote Parser

This project parses Bloomberg chat/email text into one quote record per bond quote.

Current output is for review and debugging only: JSON, XLSX, and split message JSON. Database loading can be added after LLM extraction and static-data enrichment are stable.

Extraction is performed by an OpenAI LLM using structured JSON output. Local code only splits Bloomberg messages, calls the model, performs small output cleanups, and exports files.

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="..."
```

## Run

```bash
python3 -m ficc_bbg_parser.cli /path/to/pasted-text.txt --source-date 2026-06-04 --out-dir output
```

Outputs:

- `output/quotes.json`
- `output/quotes.xlsx`
- `output/messages.json`

## Step-by-step Debugging

Run only the Bloomberg message splitter:

```bash
python3 -m ficc_bbg_parser.cli tests/email_text.txt --stage split --source-date 2026-06-04 --out-dir output
```

This writes `output/messages.json` and does not call the LLM.

Run only LLM extraction from a previously generated messages file:

```bash
python3 -m ficc_bbg_parser.cli --stage extract --messages-input output/messages.json --out-dir output --batch-size 4
```

The extractor shows batch progress by default. Disable it with `--no-progress`.

Useful tuning flags:

- `--batch-size`: number of split messages per LLM call
- `--model`: extraction model
- `--base-url`: OpenAI-compatible API base URL
- `--max-retries`: retries per LLM batch

## Parsing Layers

1. Split Bloomberg chat text into timestamped message blocks.
2. Send batches of message blocks to OpenAI with a strict JSON schema.
3. The model decides which messages contain actual bond quotes and returns one record per quote.
4. Export requested DB columns plus debug fields.

## Notes

- ISIN, issuer full name, rating, currency, and region are only filled when the chat text or context provides them.
- A later security-master enrichment layer should match `ticker + coupon_rate + maturity_date + currency` or ISIN to fill missing static fields.
- Spread quotes store `bid_spread`, `ask_spread`, and `spread_bid_ask` in bp. Price quotes store `bid_price` and `ask_price`.
