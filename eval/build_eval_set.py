import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from ficc_bbg_parser import settings
from ficc_bbg_parser.eval_store import (
    CRITICAL_FIELDS,
    EVALUATION_SCHEMA_VERSION,
)


REQUESTED_DISTRIBUTION = {
    "easy": 30,
    "missing_1": 30,
    "missing_2_3": 20,
    "missing_4_plus": 10,
    "failure_or_special": 10,
}

PREDICTION_FIELDS = (
    "ticker",
    "isin",
    "issuer",
    "currency",
    "maturity_date",
    "coupon_type",
    "coupon_rate",
    "rating_raw",
    "rating_moodys",
    "rating_sp",
    "rating_fitch",
    "bid_price",
    "ask_price",
    "bid_spread",
    "ask_spread",
    "spread_bid_ask",
    "spread_unit",
    "bid_size",
    "ask_size",
    "size_unit",
    "quote_time",
    "quote_source",
    "quote_type",
    "bond_type",
    "region",
    "remark",
    "message_id",
    "message_type",
    "source_line_no",
    "raw_line",
    "parser_name",
    "parse_status",
    "confidence",
)

WHITESPACE_RE = re.compile(r"\s+")


def main() -> None:
    args = _parse_args()
    dataset = build_eval_dataset(
        seed=args.seed,
        shortfall_policy=args.shortfall_policy,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"output={output_path}")
    print(f"samples={len(dataset['samples'])}")
    print(
        "selection_distribution="
        + json.dumps(dataset["metadata"]["selected_distribution"], ensure_ascii=False)
    )
    print(
        "actual_difficulty_distribution="
        + json.dumps(
            dataset["metadata"]["actual_difficulty_distribution"],
            ensure_ascii=False,
        )
    )
    if dataset["metadata"]["backfills"]:
        print(
            "backfills="
            + json.dumps(dataset["metadata"]["backfills"], ensure_ascii=False)
        )


def build_eval_dataset(
    seed: int = 20260728,
    shortfall_policy: str = "strict",
) -> Dict[str, Any]:
    if shortfall_policy not in {"strict", "backfill"}:
        raise ValueError("shortfall_policy must be 'strict' or 'backfill'")

    successful_rows, failure_rows, snapshot = _load_database_rows()
    successful_candidates = _successful_candidates(successful_rows)
    failure_candidates = _failure_candidates(failure_rows)

    inventory = _candidate_inventory(
        successful_rows=successful_rows,
        successful_candidates=successful_candidates,
        failure_candidates=failure_candidates,
    )
    selected: List[Dict[str, Any]] = []
    used_fingerprints: Set[str] = set()
    backfills: List[Dict[str, Any]] = []

    bucket_order = ("easy", "missing_1", "missing_2_3", "missing_4_plus")
    candidates_by_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for candidate in successful_candidates:
        candidates_by_bucket[candidate["difficulty_candidate"]].append(candidate)

    for bucket in bucket_order:
        requested_count = REQUESTED_DISTRIBUTION[bucket]
        chosen = _select_diverse(
            candidates=candidates_by_bucket[bucket],
            count=requested_count,
            seed=seed,
            salt=bucket,
            used_fingerprints=used_fingerprints,
        )
        for candidate in chosen:
            _mark_selected(candidate, bucket, "requested_bucket")
        selected.extend(chosen)
        used_fingerprints.update(item["source_fingerprint"] for item in chosen)

        shortfall = requested_count - len(chosen)
        if shortfall <= 0:
            continue
        if shortfall_policy == "strict":
            raise RuntimeError(
                _shortfall_message(
                    bucket=bucket,
                    requested=requested_count,
                    available=len(chosen),
                    inventory=inventory,
                )
            )

        replacements = _select_backfills(
            target_bucket=bucket,
            candidates_by_bucket=candidates_by_bucket,
            count=shortfall,
            seed=seed,
            used_fingerprints=used_fingerprints,
        )
        if len(replacements) < shortfall:
            raise RuntimeError(
                _shortfall_message(
                    bucket=bucket,
                    requested=requested_count,
                    available=len(chosen) + len(replacements),
                    inventory=inventory,
                )
            )
        replacement_counts = Counter(
            item["difficulty_candidate"] for item in replacements
        )
        for candidate in replacements:
            source_bucket = candidate["difficulty_candidate"]
            _mark_selected(
                candidate,
                bucket,
                "shortfall_backfill_from_{}".format(source_bucket),
            )
        selected.extend(replacements)
        used_fingerprints.update(
            item["source_fingerprint"] for item in replacements
        )
        for source_bucket, count in sorted(replacement_counts.items()):
            backfills.append(
                {
                    "requested_bucket": bucket,
                    "source_bucket": source_bucket,
                    "count": count,
                    "reason": "insufficient_candidates_in_requested_bucket",
                }
            )

    special_candidates = failure_candidates + [
        candidate
        for candidate in successful_candidates
        if candidate["special_format_reasons"]
    ]
    special_count = REQUESTED_DISTRIBUTION["failure_or_special"]
    special_chosen = _select_special(
        candidates=special_candidates,
        count=special_count,
        seed=seed,
        used_fingerprints=used_fingerprints,
    )
    for candidate in special_chosen:
        _mark_selected(candidate, "failure_or_special", "requested_bucket")
    selected.extend(special_chosen)
    used_fingerprints.update(
        item["source_fingerprint"] for item in special_chosen
    )

    special_shortfall = special_count - len(special_chosen)
    if special_shortfall > 0:
        if shortfall_policy == "strict":
            raise RuntimeError(
                _shortfall_message(
                    bucket="failure_or_special",
                    requested=special_count,
                    available=len(special_chosen),
                    inventory=inventory,
                )
            )
        fallback_pool = [
            candidate
            for candidate in successful_candidates
            if candidate["source_fingerprint"] not in used_fingerprints
        ]
        replacements = _select_diverse(
            candidates=fallback_pool,
            count=special_shortfall,
            seed=seed,
            salt="failure_or_special_backfill",
            used_fingerprints=used_fingerprints,
        )
        if len(replacements) < special_shortfall:
            raise RuntimeError(
                _shortfall_message(
                    bucket="failure_or_special",
                    requested=special_count,
                    available=len(special_chosen) + len(replacements),
                    inventory=inventory,
                )
            )
        for candidate in replacements:
            _mark_selected(
                candidate,
                "failure_or_special",
                "shortfall_backfill_from_non_special",
            )
        selected.extend(replacements)
        used_fingerprints.update(
            item["source_fingerprint"] for item in replacements
        )
        backfills.append(
            {
                "requested_bucket": "failure_or_special",
                "source_bucket": "non_special",
                "count": len(replacements),
                "reason": "insufficient_failure_or_special_candidates",
            }
        )

    expected_total = sum(REQUESTED_DISTRIBUTION.values())
    if len(selected) != expected_total:
        raise RuntimeError(
            "Expected {} samples but selected {}".format(expected_total, len(selected))
        )
    if len(used_fingerprints) != expected_total:
        raise RuntimeError("Selected samples are not unique by source fingerprint")

    selected = _order_for_annotation(selected, seed)
    samples = [
        _to_output_sample(candidate, index)
        for index, candidate in enumerate(selected, start=1)
    ]
    selected_distribution = Counter(
        sample["selection_bucket"] for sample in samples
    )
    actual_distribution = Counter(
        sample["difficulty_candidate"] for sample in samples
    )

    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "dataset_name": "ficc_quote_extraction_initial_screening",
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "shortfall_policy": shortfall_policy,
            "critical_fields": list(CRITICAL_FIELDS),
            "critical_field_count": len(CRITICAL_FIELDS),
            "issuer_evaluation_policy": (
                "issuer is optional metadata and is excluded from "
                "extraction accuracy"
            ),
            "maturity_presence_rule": (
                "maturity_date is present when either bond_quote.maturity_date "
                "or bond_quote.maturity_text is populated"
            ),
            "requested_distribution": REQUESTED_DISTRIBUTION,
            "selected_distribution": dict(sorted(selected_distribution.items())),
            "actual_difficulty_distribution": dict(
                sorted(actual_distribution.items())
            ),
            "candidate_inventory": inventory,
            "backfills": backfills,
            "database_snapshot": snapshot,
            "deduplication": (
                "Keep the latest source line for each normalized quote_line text"
            ),
            "paragraph_context_rule": "First five lines of paragraph text",
        },
        "samples": samples,
    }


def classify_missing_fields(prediction: Dict[str, Any]) -> List[str]:
    missing = []
    for field in CRITICAL_FIELDS:
        if field == "maturity_date":
            if _is_blank(prediction.get("maturity_date")) and _is_blank(
                prediction.get("maturity_text")
            ):
                missing.append(field)
        elif _is_blank(prediction.get(field)):
            missing.append(field)
    return missing


def difficulty_bucket(missing_count: int) -> str:
    if missing_count == 0:
        return "easy"
    if missing_count == 1:
        return "missing_1"
    if missing_count <= 3:
        return "missing_2_3"
    return "missing_4_plus"


def paragraph_head(text: Optional[str], max_lines: int = 5) -> str:
    if not text:
        return ""
    return "\n".join(text.splitlines()[:max_lines])


def _successful_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_fingerprint: Dict[str, Dict[str, Any]] = {}
    rows_by_quote_line: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_quote_line[int(row["quote_line_pk"])].append(row)

    for quote_line_pk, line_rows in rows_by_quote_line.items():
        line_rows.sort(key=lambda item: int(item["quote_id"]), reverse=True)
        primary = line_rows[0]
        fingerprint = _source_fingerprint(primary["quote_line"])
        predictions = [_prediction_snapshot(row) for row in line_rows]
        missing_fields = classify_missing_fields(primary)
        candidate = {
            "candidate_kind": "successful_quote",
            "candidate_id": "quote_line:{}".format(quote_line_pk),
            "source_fingerprint": fingerprint,
            "difficulty_candidate": difficulty_bucket(len(missing_fields)),
            "missing_fields_current": missing_fields,
            "missing_field_count": len(missing_fields),
            "special_format_reasons": _special_reasons(primary, line_rows),
            "quote_ids": [int(row["quote_id"]) for row in line_rows],
            "run_id": int(primary["run_id"]),
            "quote_line_pk": quote_line_pk,
            "line_id": primary["line_id"],
            "paragraph_id": primary["paragraph_id"],
            "quote_line": primary["quote_line"],
            "paragraph_context": paragraph_head(primary["paragraph_text"]),
            "paragraph_line_count": len(
                (primary["paragraph_text"] or "").splitlines()
            ),
            "quote_time": primary["line_quote_time"] or primary["quote_time"],
            "quote_source": primary["line_quote_source"]
            or primary["quote_source"],
            "speaker": primary["speaker"],
            "source_date": primary["source_date"],
            "source_line_no": primary["line_source_line_no"],
            "current_predictions": predictions,
            "extraction_failure": None,
            "ticker_for_diversity": primary.get("ticker"),
        }
        existing = latest_by_fingerprint.get(fingerprint)
        if existing is None or max(candidate["quote_ids"]) > max(existing["quote_ids"]):
            latest_by_fingerprint[fingerprint] = candidate

    return list(latest_by_fingerprint.values())


def _failure_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_fingerprint: Dict[str, Dict[str, Any]] = {}
    for row in sorted(
        rows,
        key=lambda item: int(item["failure_id"]),
        reverse=True,
    ):
        fingerprint = _source_fingerprint(row["quote_line"])
        if fingerprint in latest_by_fingerprint:
            continue
        latest_by_fingerprint[fingerprint] = {
            "candidate_kind": "extraction_failure",
            "candidate_id": "failure:{}".format(row["failure_id"]),
            "source_fingerprint": fingerprint,
            "difficulty_candidate": "extraction_failure",
            "missing_fields_current": list(CRITICAL_FIELDS),
            "missing_field_count": len(CRITICAL_FIELDS),
            "special_format_reasons": ["extraction_failure"],
            "quote_ids": [],
            "run_id": int(row["run_id"]),
            "quote_line_pk": int(row["quote_line_pk"]),
            "line_id": row["line_id"],
            "paragraph_id": row["paragraph_id"],
            "quote_line": row["quote_line"],
            "paragraph_context": paragraph_head(row["paragraph_text"]),
            "paragraph_line_count": len(
                (row["paragraph_text"] or "").splitlines()
            ),
            "quote_time": row["quote_time"],
            "quote_source": row["quote_source"],
            "speaker": row["speaker"],
            "source_date": row["source_date"],
            "source_line_no": row["source_line_no"],
            "current_predictions": [],
            "extraction_failure": {
                "failure_id": row["failure_id"],
                "model": row["model"],
                "attempts": row["attempts"],
                "error_type": row["error_type"],
                "error_message": row["error_message"],
                "failure_payload": row["failure_payload"],
                "created_at": row["failure_created_at"],
            },
            "ticker_for_diversity": None,
        }
    return list(latest_by_fingerprint.values())


def _prediction_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    prediction = {field: row.get(field) for field in PREDICTION_FIELDS}
    prediction["maturity_date"] = (
        row.get("maturity_date") or row.get("maturity_text")
    )
    prediction["storage_maturity"] = {
        "maturity_text": row.get("maturity_text"),
        "maturity_date": row.get("maturity_date"),
        "maturity_precision": row.get("maturity_precision"),
        "maturity_year": row.get("maturity_year"),
        "maturity_month": row.get("maturity_month"),
        "maturity_day": row.get("maturity_day"),
    }
    prediction["quote_id"] = row["quote_id"]
    prediction["quote_sequence"] = row["quote_sequence"]
    prediction["raw_payload"] = row["raw_payload"]
    return _json_ready(prediction)


def _special_reasons(
    primary: Dict[str, Any],
    line_rows: Sequence[Dict[str, Any]],
) -> List[str]:
    reasons = []
    if len(line_rows) > 1:
        reasons.append("multiple_predictions_from_one_quote_line")
    if any(int(row.get("quote_sequence") or 1) > 1 for row in line_rows):
        reasons.append("quote_sequence_gt_1")
    if primary.get("message_type") == "quote_list":
        reasons.append("message_type_quote_list")
    if primary.get("message_type") == "unknown":
        reasons.append("message_type_unknown")
    if primary.get("parse_status") != "parsed":
        reasons.append("parse_status_{}".format(primary.get("parse_status")))
    if primary.get("maturity_precision") in {"year", "month"}:
        reasons.append("partial_maturity")
    has_bid = primary.get("bid_price") is not None or primary.get("bid_spread") is not None
    has_ask = primary.get("ask_price") is not None or primary.get("ask_spread") is not None
    if has_bid != has_ask:
        reasons.append("one_sided_quote")
    return reasons


def _select_diverse(
    candidates: Iterable[Dict[str, Any]],
    count: int,
    seed: int,
    salt: str,
    used_fingerprints: Set[str],
    preserve_order: bool = False,
) -> List[Dict[str, Any]]:
    available = [
        item
        for item in candidates
        if item["source_fingerprint"] not in used_fingerprints
    ]
    if not preserve_order:
        available.sort(
            key=lambda item: _stable_rank(seed, salt, item["source_fingerprint"])
        )
    selected: List[Dict[str, Any]] = []
    ticker_counts: Counter = Counter()
    source_counts: Counter = Counter()

    for candidate in available:
        ticker = candidate.get("ticker_for_diversity") or "<missing>"
        source = candidate.get("quote_source") or "<missing>"
        if ticker_counts[ticker] >= 2 or source_counts[source] >= 5:
            continue
        selected.append(candidate)
        ticker_counts[ticker] += 1
        source_counts[source] += 1
        if len(selected) == count:
            return selected

    selected_fingerprints = {
        candidate["source_fingerprint"] for candidate in selected
    }
    for candidate in available:
        if candidate["source_fingerprint"] in selected_fingerprints:
            continue
        selected.append(candidate)
        if len(selected) == count:
            break
    return selected


def _select_backfills(
    target_bucket: str,
    candidates_by_bucket: Dict[str, List[Dict[str, Any]]],
    count: int,
    seed: int,
    used_fingerprints: Set[str],
) -> List[Dict[str, Any]]:
    nearest_order = {
        "easy": ("missing_1", "missing_2_3", "missing_4_plus"),
        "missing_1": ("missing_2_3", "easy", "missing_4_plus"),
        "missing_2_3": ("missing_4_plus", "missing_1", "easy"),
        "missing_4_plus": ("missing_2_3", "missing_1", "easy"),
    }
    selected: List[Dict[str, Any]] = []
    local_used = set(used_fingerprints)
    for source_bucket in nearest_order[target_bucket]:
        bucket_candidates = candidates_by_bucket[source_bucket]
        if target_bucket == "missing_4_plus" and source_bucket == "missing_2_3":
            bucket_candidates = sorted(
                bucket_candidates,
                key=lambda item: (
                    -int(item["missing_field_count"]),
                    _stable_rank(
                        seed,
                        "{}_{}".format(target_bucket, source_bucket),
                        item["source_fingerprint"],
                    ),
                ),
            )
        chosen = _select_diverse(
            candidates=bucket_candidates,
            count=count - len(selected),
            seed=seed,
            salt="{}_backfill_{}".format(target_bucket, source_bucket),
            used_fingerprints=local_used,
            preserve_order=(
                target_bucket == "missing_4_plus"
                and source_bucket == "missing_2_3"
            ),
        )
        selected.extend(chosen)
        local_used.update(item["source_fingerprint"] for item in chosen)
        if len(selected) == count:
            break
    return selected


def _select_special(
    candidates: Iterable[Dict[str, Any]],
    count: int,
    seed: int,
    used_fingerprints: Set[str],
) -> List[Dict[str, Any]]:
    available = [
        item
        for item in candidates
        if item["source_fingerprint"] not in used_fingerprints
    ]
    selected: List[Dict[str, Any]] = []
    selected_fingerprints: Set[str] = set()

    failure_pool = [
        item
        for item in available
        if "extraction_failure" in item["special_format_reasons"]
    ]
    failure_limit = min(len(failure_pool), max(1, count // 2))
    failures = _select_diverse(
        candidates=failure_pool,
        count=failure_limit,
        seed=seed,
        salt="extraction_failure",
        used_fingerprints=used_fingerprints,
    )
    selected.extend(failures)
    selected_fingerprints.update(
        item["source_fingerprint"] for item in failures
    )

    reason_order = (
        "multiple_predictions_from_one_quote_line",
        "quote_sequence_gt_1",
        "parse_status_needs_review",
        "message_type_unknown",
        "message_type_quote_list",
        "partial_maturity",
        "one_sided_quote",
    )
    reason_pools = {}
    for reason in reason_order:
        reason_pools[reason] = sorted(
            [
                item
                for item in available
                if reason in item["special_format_reasons"]
            ],
            key=lambda item: _stable_rank(
                seed,
                "special_reason_{}".format(reason),
                item["source_fingerprint"],
            ),
        )

    while len(selected) < count:
        made_progress = False
        for reason in reason_order:
            candidate = next(
                (
                    item
                    for item in reason_pools[reason]
                    if item["source_fingerprint"] not in selected_fingerprints
                ),
                None,
            )
            if candidate is None:
                continue
            selected.append(candidate)
            selected_fingerprints.add(candidate["source_fingerprint"])
            made_progress = True
            if len(selected) == count:
                return selected
        if not made_progress:
            break

    remaining_used = set(used_fingerprints)
    remaining_used.update(selected_fingerprints)
    fillers = _select_diverse(
        candidates=available,
        count=count - len(selected),
        seed=seed,
        salt="failure_or_special_fill",
        used_fingerprints=remaining_used,
    )
    selected.extend(fillers)
    return selected


def _mark_selected(
    candidate: Dict[str, Any],
    selection_bucket: str,
    selection_reason: str,
) -> None:
    candidate["selection_bucket"] = selection_bucket
    candidate["selection_reason"] = selection_reason


def _order_for_annotation(
    selected: List[Dict[str, Any]],
    seed: int,
) -> List[Dict[str, Any]]:
    ordered = list(selected)
    random.Random(seed).shuffle(ordered)
    return ordered


def _to_output_sample(
    candidate: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    return _json_ready(
        {
            "sample_id": "eval_{:04d}".format(index),
            "selection_bucket": candidate["selection_bucket"],
            "selection_reason": candidate["selection_reason"],
            "difficulty_candidate": candidate["difficulty_candidate"],
            "missing_fields_current": candidate["missing_fields_current"],
            "missing_field_count": candidate["missing_field_count"],
            "special_format_reasons": candidate["special_format_reasons"],
            "source": {
                "candidate_kind": candidate["candidate_kind"],
                "candidate_id": candidate["candidate_id"],
                "source_fingerprint": candidate["source_fingerprint"],
                "quote_ids": candidate["quote_ids"],
                "run_id": candidate["run_id"],
                "quote_line_pk": candidate["quote_line_pk"],
                "line_id": candidate["line_id"],
                "paragraph_id": candidate["paragraph_id"],
                "quote_time": candidate["quote_time"],
                "quote_source": candidate["quote_source"],
                "speaker": candidate["speaker"],
                "source_date": candidate["source_date"],
                "source_line_no": candidate["source_line_no"],
            },
            "input": {
                "quote_line": candidate["quote_line"],
                "paragraph_context": candidate["paragraph_context"],
                "paragraph_context_line_limit": 5,
                "paragraph_line_count": candidate["paragraph_line_count"],
            },
            "prediction_snapshot": candidate["current_predictions"],
            "extraction_failure": candidate["extraction_failure"],
            "annotation": {
                "status": "pending",
                "difficulty_gold": None,
                "field_availability": {
                    field: "pending" for field in CRITICAL_FIELDS
                },
                "gold": {field: None for field in CRITICAL_FIELDS},
                "annotator": None,
                "remark": None,
            },
        }
    )


def _candidate_inventory(
    successful_rows: List[Dict[str, Any]],
    successful_candidates: List[Dict[str, Any]],
    failure_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_distribution = Counter()
    for row in successful_rows:
        raw_distribution[
            difficulty_bucket(len(classify_missing_fields(row)))
        ] += 1
    deduplicated_distribution = Counter(
        item["difficulty_candidate"] for item in successful_candidates
    )
    special_success_count = sum(
        1 for item in successful_candidates if item["special_format_reasons"]
    )
    return {
        "successful_quote_rows": len(successful_rows),
        "successful_unique_source_lines": len(successful_candidates),
        "failure_unique_source_lines": len(failure_candidates),
        "raw_row_distribution": dict(sorted(raw_distribution.items())),
        "deduplicated_distribution": dict(
            sorted(deduplicated_distribution.items())
        ),
        "failure_or_special_eligible": (
            len(failure_candidates) + special_success_count
        ),
    }


def _shortfall_message(
    bucket: str,
    requested: int,
    available: int,
    inventory: Dict[str, Any],
) -> str:
    return (
        "Insufficient candidates for bucket {!r}: requested {}, selected {}. "
        "Inventory: {}. Re-run with --shortfall-policy backfill for an "
        "auditable nearest-bucket replacement."
    ).format(bucket, requested, available, json.dumps(inventory, ensure_ascii=False))


def _stable_rank(seed: int, salt: str, fingerprint: str) -> str:
    value = "{}|{}|{}".format(seed, salt, fingerprint)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_fingerprint(text: Optional[str]) -> str:
    normalized = WHITESPACE_RE.sub(" ", (text or "").strip()).upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _load_database_rows() -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, Any],
]:
    with _dict_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                q.*,
                l.line_id,
                l.text AS quote_line,
                l.quote_time AS line_quote_time,
                l.quote_source AS line_quote_source,
                l.speaker,
                l.source_date,
                l.source_line_no AS line_source_line_no,
                p.paragraph_id,
                p.text AS paragraph_text
            FROM bond_quote q
            JOIN quote_line l ON l.quote_line_pk = q.quote_line_pk
            JOIN chat_paragraph p ON p.paragraph_pk = l.paragraph_pk
            ORDER BY q.quote_id DESC
            """
        )
        successful_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT
                f.failure_id,
                f.run_id,
                f.quote_line_pk,
                f.model,
                f.attempts,
                f.error_type,
                f.error_message,
                f.failure_payload,
                f.created_at AS failure_created_at,
                l.line_id,
                l.text AS quote_line,
                l.quote_time,
                l.quote_source,
                l.speaker,
                l.source_date,
                l.source_line_no,
                p.paragraph_id,
                p.text AS paragraph_text
            FROM quote_extraction_failure f
            JOIN quote_line l ON l.quote_line_pk = f.quote_line_pk
            JOIN chat_paragraph p ON p.paragraph_pk = l.paragraph_pk
            ORDER BY f.failure_id DESC
            """
        )
        failure_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT
                current_database() AS database_name,
                now() AS queried_at,
                (SELECT max(quote_id) FROM bond_quote) AS max_quote_id,
                (SELECT max(failure_id) FROM quote_extraction_failure)
                    AS max_failure_id,
                (SELECT max(run_id) FROM ingestion_run) AS max_run_id
            """
        )
        snapshot = cursor.fetchone()
    snapshot["schema"] = settings.POSTGRES_SCHEMA
    return successful_rows, failure_rows, _json_ready(snapshot)


@contextmanager
def _dict_cursor() -> Iterator[Any]:
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
        with connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                psycopg.sql.SQL("SET search_path TO {}, public").format(
                    psycopg.sql.Identifier(settings.POSTGRES_SCHEMA)
                )
            )
            yield cursor


def _import_psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise ImportError(
            "psycopg is required. Install the root requirements.txt."
        ) from exc
    return psycopg


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a stratified JSON evaluation set from PostgreSQL."
    )
    parser.add_argument(
        "--output",
        default="eval/eval_dataset.json",
        help="JSON output path (default: eval/eval_dataset.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260728,
        help="Deterministic sampling seed (default: 20260728)",
    )
    parser.add_argument(
        "--shortfall-policy",
        choices=("strict", "backfill"),
        default="strict",
        help=(
            "strict fails when a bucket is undersupplied; backfill uses the "
            "nearest bucket and records every replacement"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
