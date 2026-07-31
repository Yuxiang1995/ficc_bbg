# Quote Extraction Evaluation Set

This directory contains the PostgreSQL sampling tool, its tests, generated JSON dataset, and future annotation/evaluation code.

`eval_dataset.json` contains original institutional quote text and context. Keep the repository and any exported copies inside the approved internal environment.

## Sampling Definition

The default requested distribution is:

| Selection bucket | Count |
|---|---:|
| All five critical fields populated | 30 |
| One critical field missing | 30 |
| Two or three critical fields missing | 20 |
| Four or more critical fields missing | 10 |
| Extraction failure or special format | 10 |

The five critical fields are `ticker`, `currency`, `maturity_date`,
`coupon_type`, and `coupon_rate`. `issuer` remains available in extraction
output as optional metadata, but it does not participate in sample difficulty
or accuracy scoring.

For maturity, either `bond_quote.maturity_date` or `bond_quote.maturity_text` counts as populated. This prevents partial values such as `2029-09` from being treated as missing.

The dataset is deduplicated by normalized quote-line text. Sampling uses a fixed seed and attempts to diversify tickers and quote sources. Each sample includes the quote line, the first five paragraph lines used by the production extractor, the current prediction snapshot, database IDs, missing-field information, and an empty manual-annotation section.

## Build

Strict mode refuses to change the requested distribution:

```bash
python -m eval.build_eval_set \
  --output eval/eval_dataset.json \
  --seed 20260728 \
  --shortfall-policy strict
```

For an initial screening set, auditable backfill mode fills an undersupplied bucket from the nearest difficulty bucket:

```bash
python -m eval.build_eval_set \
  --output eval/eval_dataset.json \
  --seed 20260728 \
  --shortfall-policy backfill
```

Backfilled samples retain their real `difficulty_candidate`. Their requested quota is stored in `selection_bucket`, and `selection_reason` identifies the source bucket. Aggregate replacements are listed in `metadata.backfills`.

The script opens a read-only PostgreSQL transaction and uses the root `.env` connection settings. It never updates business tables.

## Test

```bash
python -m unittest eval.test_build_eval_set -v
python -m unittest eval.test_eval_store -v
python -m unittest eval.test_run_inference -v
python -m unittest eval.test_score_eval -v
```

## Annotation Page

Build the frontend and start the API:

```bash
cd front
npm ci
npm run build
cd ..

python -m uvicorn ficc_bbg_parser.api:app \
  --host 127.0.0.1 \
  --port 8000
```

Open `http://127.0.0.1:8000/#/annotation`.

The paths default to:

```dotenv
EVAL_DATASET_PATH=eval/eval_dataset.json
EVAL_ANNOTATED_PATH=eval/eval_annotated.json
```

The page always reads `eval_annotated.json` when it already exists, allowing an
annotation session to resume after a restart. Otherwise it begins from
`eval_dataset.json`. The first save creates `eval_annotated.json` atomically;
the source dataset is never changed.

For every sample, the page displays the quote line, first five paragraph lines,
current prediction, and editable Gold values. It also provides status filters,
progress, draft saving, completed-state validation, and JSON download.

## Annotation Fields

For every sample, complete:

- `annotation.status`: change from `pending` to `completed`
- `annotation.difficulty_gold`: `easy`, `hard_extraction`, `hard_context`, `unanswerable`, or `ambiguous`
- `annotation.field_availability`: set each field to `present`, `inferable`, `absent`, or `ambiguous`
- `annotation.gold`: enter the manually verified value for each critical field
- `annotation.annotator` and `annotation.remark`

The top-level metadata freezes the database snapshot, random seed, candidate inventory, requested/selected distributions, and backfill information.

## Run Current Extractor

After completing the `annotation` section, run the current production extractor
against the frozen inputs:

```bash
python -m eval.run_inference \
  --input eval/eval_annotated.json \
  --output eval/eval_inference.json \
  --batch-size 10 \
  --workers 4 \
  --max-retries 2 \
  --timeout 90
```

The runner uses `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` /
`MID_MODEL` from the root `.env`. Command-line `--model` and `--base-url`
override those values.

The input and output must be different files. Existing output files are protected
unless `--overwrite-output` is supplied. The runner:

- Reuses `OpenAIQuoteExtractor`, including its production prompt, retry, batching,
  paragraph-context, and concurrency behavior
- Groups samples from the same original run and paragraph into one extractor
  paragraph, with oversized groups still split by `--batch-size`
- Replaces `prediction_snapshot` only in the new inference output
- Preserves every manual `annotation` and the historical `extraction_failure`
- Records the current run in `metadata.inference`
- Records exhausted retries in `inference_failure` and continues with other samples

Each sample receives an `inference_status`: `predicted`,
`multiple_predictions`, `predicted_with_failure`, `failed`, or
`no_prediction`.

## Score

Score the newly generated prediction snapshot against the preserved gold fields:

```bash
python -m eval.score_eval \
  --input eval/eval_inference.json \
  --output eval/eval_report.json \
  --strict-annotations
```

Only samples with `annotation.status = completed` are scored. `pending` samples are excluded. If no completed samples exist, accuracy values are `null` rather than `0`.

The report contains:

- Micro field accuracy across all evaluated critical fields
- Answerable-field accuracy for `present` / `inferable` fields
- Absent-field null accuracy and overall null-decision accuracy
- Macro sample field accuracy
- Exact-record accuracy over all evaluable fields
- All-critical-fields exact-record accuracy
- Per-field accuracy and null correctness
- Omission, hallucination, and wrong-value counts
- Metrics grouped by selection bucket, candidate difficulty, and gold difficulty
- Annotation validation errors and field-level error details

Comparison normalization includes uppercase ticker/currency values,
`Fixed`/`FRN` coupon aliases, common full/partial maturity formats, and an
absolute coupon-rate tolerance of `1e-6`.

Field scoring follows `annotation.field_availability`:

- `present` / `inferable`: gold must be non-null
- `absent`: gold must be null; a generated value is a hallucination
- `ambiguous`: excluded from accuracy
- `pending`: invalid once the sample status is `completed`

An empty `prediction_snapshot` is treated as six null predictions, allowing extraction failures to be scored. Multiple prediction records require a future multi-record gold schema and are currently reported as annotation validation errors instead of being matched optimistically.
