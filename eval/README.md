# Quote Extraction Evaluation Set

This directory contains the PostgreSQL sampling tool, its tests, generated JSON dataset, and future annotation/evaluation code.

`eval_dataset.json` contains original institutional quote text and context. Keep the repository and any exported copies inside the approved internal environment.

## Sampling Definition

The default requested distribution is:

| Selection bucket | Count |
|---|---:|
| All six critical fields populated | 30 |
| One critical field missing | 30 |
| Two or three critical fields missing | 20 |
| Four or more critical fields missing | 10 |
| Extraction failure or special format | 10 |

The six critical fields are `ticker`, `issuer`, `currency`, `maturity_date`, `coupon_type`, and `coupon_rate`.

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
```

## Manual Annotation

For every sample, complete:

- `annotation.status`: change from `pending` to `completed`
- `annotation.difficulty_gold`: `easy`, `hard_extraction`, `hard_context`, `unanswerable`, or `ambiguous`
- `annotation.field_availability`: set each field to `present`, `inferable`, `absent`, or `ambiguous`
- `annotation.gold`: enter the manually verified value for each critical field
- `annotation.annotator` and `annotation.remark`

The top-level metadata freezes the database snapshot, random seed, candidate inventory, requested/selected distributions, and backfill information.
