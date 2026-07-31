import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .build_eval_set import CRITICAL_FIELDS


VALID_AVAILABILITY = {"present", "inferable", "absent", "ambiguous"}
WHITESPACE_RE = re.compile(r"\s+")


def main() -> None:
    args = _parse_args()
    input_path = Path(args.input)
    dataset = json.loads(input_path.read_text(encoding="utf-8"))
    report = score_dataset(
        dataset=dataset,
        coupon_rate_tolerance=args.coupon_rate_tolerance,
        strict_annotations=args.strict_annotations,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    coverage = report["annotation_coverage"]
    overall = report["metrics"]["overall"]
    print(f"output={output_path}")
    print(f"total_samples={coverage['total_samples']}")
    print(f"completed_annotations={coverage['completed_annotations']}")
    print(f"evaluated_samples={coverage['evaluated_samples']}")
    print(f"micro_field_accuracy={_format_metric(overall['micro_field_accuracy'])}")
    print(f"record_exact_accuracy={_format_metric(overall['record_exact_accuracy'])}")
    print(f"validation_errors={len(report['validation_errors'])}")
    if coverage["evaluated_samples"] == 0:
        print(
            "warning=no completed and valid annotations; "
            "accuracy metrics are null, not zero"
        )


def score_dataset(
    dataset: Dict[str, Any],
    coupon_rate_tolerance: float = 1e-6,
    strict_annotations: bool = False,
) -> Dict[str, Any]:
    samples = dataset.get("samples")
    if not isinstance(samples, list):
        raise ValueError("Dataset must contain a samples list")
    if coupon_rate_tolerance < 0:
        raise ValueError("coupon_rate_tolerance must be non-negative")

    status_counts: Counter = Counter()
    validation_errors: List[Dict[str, Any]] = []
    field_stats = {
        field: Counter(
            {
                "evaluated": 0,
                "correct": 0,
                "incorrect": 0,
                "correct_null": 0,
                "omission": 0,
                "hallucination": 0,
                "wrong_value": 0,
                "ambiguous_excluded": 0,
            }
        )
        for field in CRITICAL_FIELDS
    }
    availability_counts = {
        field: Counter() for field in CRITICAL_FIELDS
    }
    errors: List[Dict[str, Any]] = []
    availability_accuracy: Counter = Counter(
        {
            "answerable_evaluated": 0,
            "answerable_correct": 0,
            "absent_evaluated": 0,
            "absent_correct": 0,
            "null_decision_evaluated": 0,
            "null_decision_correct": 0,
        }
    )
    overall = _new_group_accumulator()
    groups: Dict[str, Dict[str, Dict[str, Any]]] = {
        "selection_bucket": defaultdict(_new_group_accumulator),
        "difficulty_candidate": defaultdict(_new_group_accumulator),
        "difficulty_gold": defaultdict(_new_group_accumulator),
    }
    completed_annotations = 0
    invalid_completed_samples: set = set()

    for sample in samples:
        sample_id = str(sample.get("sample_id") or "<missing_sample_id>")
        annotation = sample.get("annotation")
        if not isinstance(annotation, dict):
            status_counts["missing_annotation"] += 1
            validation_errors.append(
                _validation_error(sample_id, None, "annotation must be an object")
            )
            continue

        status = str(annotation.get("status") or "pending")
        status_counts[status] += 1
        if status != "completed":
            continue
        completed_annotations += 1

        prediction, prediction_error = _resolve_prediction(sample)
        if prediction_error:
            validation_errors.append(
                _validation_error(sample_id, None, prediction_error)
            )
            invalid_completed_samples.add(sample_id)
            continue

        gold = annotation.get("gold")
        availability_map = annotation.get("field_availability")
        if not isinstance(gold, dict):
            validation_errors.append(
                _validation_error(sample_id, None, "annotation.gold must be an object")
            )
            invalid_completed_samples.add(sample_id)
            continue
        if not isinstance(availability_map, dict):
            validation_errors.append(
                _validation_error(
                    sample_id,
                    None,
                    "annotation.field_availability must be an object",
                )
            )
            invalid_completed_samples.add(sample_id)
            continue

        sample_results: List[bool] = []
        sample_field_errors = 0
        for field in CRITICAL_FIELDS:
            availability = availability_map.get(field)
            if availability not in VALID_AVAILABILITY:
                validation_errors.append(
                    _validation_error(
                        sample_id,
                        field,
                        "availability must be one of {}".format(
                            ", ".join(sorted(VALID_AVAILABILITY))
                        ),
                    )
                )
                sample_field_errors += 1
                continue
            availability_counts[field][availability] += 1
            if availability == "ambiguous":
                field_stats[field]["ambiguous_excluded"] += 1
                continue

            gold_value = gold.get(field)
            if availability in {"present", "inferable"} and _is_blank(gold_value):
                validation_errors.append(
                    _validation_error(
                        sample_id,
                        field,
                        "{} field requires a non-null gold value".format(availability),
                    )
                )
                sample_field_errors += 1
                continue
            if availability == "absent" and not _is_blank(gold_value):
                validation_errors.append(
                    _validation_error(
                        sample_id,
                        field,
                        "absent field must use null as the gold value",
                    )
                )
                sample_field_errors += 1
                continue

            prediction_value = prediction.get(field)
            normalized_prediction = normalize_value(field, prediction_value)
            normalized_gold = normalize_value(field, gold_value)
            correct = values_equal(
                field=field,
                prediction=normalized_prediction,
                gold=normalized_gold,
                coupon_rate_tolerance=coupon_rate_tolerance,
            )
            sample_results.append(correct)
            if availability in {"present", "inferable"}:
                availability_accuracy["answerable_evaluated"] += 1
                availability_accuracy["answerable_correct"] += int(correct)
            else:
                availability_accuracy["absent_evaluated"] += 1
                availability_accuracy["absent_correct"] += int(correct)
            availability_accuracy["null_decision_evaluated"] += 1
            availability_accuracy["null_decision_correct"] += int(
                (normalized_prediction is None) == (normalized_gold is None)
            )

            stats = field_stats[field]
            stats["evaluated"] += 1
            if correct:
                stats["correct"] += 1
                if normalized_prediction is None:
                    stats["correct_null"] += 1
                continue

            stats["incorrect"] += 1
            error_type = _error_type(
                availability=availability,
                prediction=normalized_prediction,
            )
            stats[error_type] += 1
            errors.append(
                {
                    "sample_id": sample_id,
                    "field": field,
                    "error_type": error_type,
                    "availability": availability,
                    "prediction": _json_ready(prediction_value),
                    "gold": _json_ready(gold_value),
                    "normalized_prediction": _json_ready(normalized_prediction),
                    "normalized_gold": _json_ready(normalized_gold),
                    "selection_bucket": sample.get("selection_bucket"),
                    "difficulty_candidate": sample.get("difficulty_candidate"),
                    "difficulty_gold": annotation.get("difficulty_gold"),
                    "quote_line": (sample.get("input") or {}).get("quote_line"),
                }
            )

        if sample_field_errors:
            invalid_completed_samples.add(sample_id)
        if not sample_results:
            continue

        exact = all(sample_results)
        sample_accuracy = sum(sample_results) / len(sample_results)
        fully_evaluable = len(sample_results) == len(CRITICAL_FIELDS)
        _update_group(
            overall,
            field_results=sample_results,
            exact=exact,
            sample_accuracy=sample_accuracy,
            fully_evaluable=fully_evaluable,
        )
        for group_name, label in (
            ("selection_bucket", sample.get("selection_bucket")),
            ("difficulty_candidate", sample.get("difficulty_candidate")),
            ("difficulty_gold", annotation.get("difficulty_gold") or "unlabeled"),
        ):
            _update_group(
                groups[group_name][str(label or "unlabeled")],
                field_results=sample_results,
                exact=exact,
                sample_accuracy=sample_accuracy,
                fully_evaluable=fully_evaluable,
            )

    if strict_annotations and validation_errors:
        first = validation_errors[0]
        raise ValueError(
            "Invalid annotation: sample_id={}, field={}, message={}".format(
                first["sample_id"],
                first["field"],
                first["message"],
            )
        )

    total_samples = len(samples)
    evaluated_samples = overall["sample_count"]
    overall_metrics = _finalize_group(overall)
    overall_metrics.update(
        {
            "answerable_fields": availability_accuracy["answerable_evaluated"],
            "answerable_correct": availability_accuracy["answerable_correct"],
            "answerable_field_accuracy": _safe_ratio(
                availability_accuracy["answerable_correct"],
                availability_accuracy["answerable_evaluated"],
            ),
            "absent_fields": availability_accuracy["absent_evaluated"],
            "correct_absent_nulls": availability_accuracy["absent_correct"],
            "absent_field_null_accuracy": _safe_ratio(
                availability_accuracy["absent_correct"],
                availability_accuracy["absent_evaluated"],
            ),
            "null_decision_accuracy": _safe_ratio(
                availability_accuracy["null_decision_correct"],
                availability_accuracy["null_decision_evaluated"],
            ),
        }
    )
    report = {
        "schema_version": "1.0",
        "report_name": "ficc_quote_extraction_score",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": {
            "dataset_name": dataset.get("dataset_name"),
            "dataset_schema_version": dataset.get("schema_version"),
            "seed": (dataset.get("metadata") or {}).get("seed"),
            "database_snapshot": (dataset.get("metadata") or {}).get(
                "database_snapshot"
            ),
        },
        "configuration": {
            "critical_fields": list(CRITICAL_FIELDS),
            "coupon_rate_tolerance": coupon_rate_tolerance,
            "included_annotation_status": "completed",
            "excluded_availability": ["ambiguous"],
            "maturity_comparison": "ISO and common partial-date normalization",
        },
        "annotation_coverage": {
            "total_samples": total_samples,
            "status_counts": dict(sorted(status_counts.items())),
            "completed_annotations": completed_annotations,
            "evaluated_samples": evaluated_samples,
            "invalid_completed_samples": len(invalid_completed_samples),
            "coverage": _safe_ratio(evaluated_samples, total_samples),
        },
        "metrics": {
            "overall": overall_metrics,
            "per_field": {
                field: _finalize_field(
                    field_stats[field],
                    availability_counts[field],
                )
                for field in CRITICAL_FIELDS
            },
            "by_selection_bucket": _finalize_groups(
                groups["selection_bucket"]
            ),
            "by_difficulty_candidate": _finalize_groups(
                groups["difficulty_candidate"]
            ),
            "by_difficulty_gold": _finalize_groups(groups["difficulty_gold"]),
        },
        "error_summary": dict(
            sorted(Counter(item["error_type"] for item in errors).items())
        ),
        "validation_errors": validation_errors,
        "errors": errors,
    }
    return _json_ready(report)


def normalize_value(field: str, value: Any) -> Any:
    if _is_blank(value):
        return None
    if field in {"ticker", "currency"}:
        return WHITESPACE_RE.sub(" ", str(value).strip()).upper()
    if field == "coupon_type":
        normalized = WHITESPACE_RE.sub(" ", str(value).strip()).casefold()
        if normalized in {"fixed", "fix", "fixed rate", "fixed-rate"}:
            return "Fixed"
        if normalized in {
            "frn",
            "float",
            "floating",
            "floating rate",
            "floating-rate",
        }:
            return "FRN"
        return str(value).strip()
    if field == "coupon_rate":
        try:
            return Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return str(value).strip()
    if field == "maturity_date":
        return _normalize_maturity(value)
    return value


def values_equal(
    field: str,
    prediction: Any,
    gold: Any,
    coupon_rate_tolerance: float = 1e-6,
) -> bool:
    if prediction is None or gold is None:
        return prediction is None and gold is None
    if field == "coupon_rate" and isinstance(prediction, Decimal) and isinstance(
        gold, Decimal
    ):
        tolerance = Decimal(str(coupon_rate_tolerance))
        return abs(prediction - gold) <= tolerance
    return prediction == gold


def _normalize_maturity(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    normalized = text.replace(".", "-").replace("/", "-")

    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return text

    match = re.fullmatch(r"(\d{4})-(\d{1,2})", normalized)
    if match:
        year, month = (int(part) for part in match.groups())
        if 1 <= month <= 12:
            return "{:04d}-{:02d}".format(year, month)
        return text

    if re.fullmatch(r"\d{4}", normalized):
        return normalized

    match = re.fullmatch(r"(\d{1,2})-(\d{2})", normalized)
    if match:
        month, short_year = (int(part) for part in match.groups())
        if 1 <= month <= 12:
            year = 2000 + short_year if short_year < 70 else 1900 + short_year
            return "{:04d}-{:02d}".format(year, month)

    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{2})", normalized)
    if match:
        month, day, short_year = (int(part) for part in match.groups())
        year = 2000 + short_year if short_year < 70 else 1900 + short_year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return text
    return text


def _resolve_prediction(
    sample: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[str]]:
    predictions = sample.get("prediction_snapshot")
    if not isinstance(predictions, list):
        return {}, "prediction_snapshot must be a list"
    if len(predictions) == 0:
        return {field: None for field in CRITICAL_FIELDS}, None
    if len(predictions) > 1:
        return (
            {},
            "multiple prediction records are not supported by single-record gold",
        )
    if not isinstance(predictions[0], dict):
        return {}, "prediction_snapshot item must be an object"
    return predictions[0], None


def _error_type(availability: str, prediction: Any) -> str:
    if availability in {"present", "inferable"} and prediction is None:
        return "omission"
    if availability == "absent" and prediction is not None:
        return "hallucination"
    return "wrong_value"


def _new_group_accumulator() -> Dict[str, Any]:
    return {
        "sample_count": 0,
        "field_count": 0,
        "correct_field_count": 0,
        "exact_sample_count": 0,
        "sample_accuracy_sum": 0.0,
        "fully_evaluable_sample_count": 0,
        "all_critical_fields_exact_count": 0,
    }


def _update_group(
    group: Dict[str, Any],
    field_results: Sequence[bool],
    exact: bool,
    sample_accuracy: float,
    fully_evaluable: bool,
) -> None:
    group["sample_count"] += 1
    group["field_count"] += len(field_results)
    group["correct_field_count"] += sum(field_results)
    group["exact_sample_count"] += int(exact)
    group["sample_accuracy_sum"] += sample_accuracy
    group["fully_evaluable_sample_count"] += int(fully_evaluable)
    group["all_critical_fields_exact_count"] += int(
        fully_evaluable and exact
    )


def _finalize_group(group: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evaluated_samples": group["sample_count"],
        "evaluated_fields": group["field_count"],
        "correct_fields": group["correct_field_count"],
        "micro_field_accuracy": _safe_ratio(
            group["correct_field_count"],
            group["field_count"],
        ),
        "macro_sample_field_accuracy": _safe_ratio(
            group["sample_accuracy_sum"],
            group["sample_count"],
        ),
        "exact_records": group["exact_sample_count"],
        "record_exact_accuracy": _safe_ratio(
            group["exact_sample_count"],
            group["sample_count"],
        ),
        "fully_evaluable_samples": group[
            "fully_evaluable_sample_count"
        ],
        "all_critical_fields_exact_records": group[
            "all_critical_fields_exact_count"
        ],
        "all_critical_fields_exact_accuracy": _safe_ratio(
            group["all_critical_fields_exact_count"],
            group["fully_evaluable_sample_count"],
        ),
    }


def _finalize_field(
    stats: Counter,
    availability: Counter,
) -> Dict[str, Any]:
    return {
        "evaluated": stats["evaluated"],
        "correct": stats["correct"],
        "incorrect": stats["incorrect"],
        "accuracy": _safe_ratio(stats["correct"], stats["evaluated"]),
        "correct_null": stats["correct_null"],
        "omission": stats["omission"],
        "hallucination": stats["hallucination"],
        "wrong_value": stats["wrong_value"],
        "ambiguous_excluded": stats["ambiguous_excluded"],
        "availability_counts": dict(sorted(availability.items())),
    }


def _finalize_groups(
    groups: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    return {
        name: _finalize_group(group)
        for name, group in sorted(groups.items())
    }


def _validation_error(
    sample_id: str,
    field: Optional[str],
    message: str,
) -> Dict[str, Any]:
    return {
        "sample_id": sample_id,
        "field": field,
        "message": message,
    }


def _safe_ratio(numerator: float, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / denominator


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


def _format_metric(value: Optional[float]) -> str:
    if value is None:
        return "null"
    return "{:.4%}".format(value)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score completed manual annotations in an evaluation JSON."
    )
    parser.add_argument(
        "--input",
        default="eval/eval_dataset.json",
        help="Annotated evaluation dataset JSON",
    )
    parser.add_argument(
        "--output",
        default="eval/eval_report.json",
        help="Score report JSON (default: eval/eval_report.json)",
    )
    parser.add_argument(
        "--coupon-rate-tolerance",
        type=float,
        default=1e-6,
        help="Absolute coupon-rate comparison tolerance (default: 1e-6)",
    )
    parser.add_argument(
        "--strict-annotations",
        action="store_true",
        help="Fail when any completed annotation is invalid",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
