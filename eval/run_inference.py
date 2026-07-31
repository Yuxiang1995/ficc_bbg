import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ficc_bbg_parser.llm_extractor import OpenAIQuoteExtractor, SYSTEM_PROMPT
from ficc_bbg_parser.models import (
    ExtractionFailure,
    ParagraphBlock,
    QuoteLine,
    QuoteRecord,
)


DEFAULT_INPUT = Path("eval/eval_dataset.json")
DEFAULT_OUTPUT = Path("eval/eval_inference.json")
LINE_ID_RE = re.compile(r"(?:^|;\s*)line_id=(eval_line_\d+)(?:;|$)")


def main() -> None:
    args = _parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    _validate_paths(input_path, output_path, args.overwrite_output)

    input_bytes = input_path.read_bytes()
    dataset = json.loads(input_bytes.decode("utf-8"))
    extractor = OpenAIQuoteExtractor(
        model=args.model,
        batch_size=args.batch_size,
        max_workers=args.workers,
        max_retries=args.max_retries,
        request_timeout=args.timeout,
        show_progress=not args.no_progress,
        base_url=args.base_url,
    )
    result = run_inference(
        dataset=dataset,
        extractor=extractor,
        input_dataset_sha256=hashlib.sha256(input_bytes).hexdigest(),
    )
    _write_json_atomic(output_path, result)

    inference = result["metadata"]["inference"]
    print(f"output={output_path}")
    print(f"model={inference['model']}")
    print(f"samples={inference['sample_count']}")
    print(f"predicted_samples={inference['status_counts'].get('predicted', 0)}")
    print(
        "multiple_prediction_samples="
        f"{inference['status_counts'].get('multiple_predictions', 0)}"
    )
    print(f"failed_samples={inference['status_counts'].get('failed', 0)}")
    print(
        "no_prediction_samples="
        f"{inference['status_counts'].get('no_prediction', 0)}"
    )
    print(f"unmapped_predictions={len(inference['unmapped_predictions'])}")
    print(f"unmapped_failures={len(inference['unmapped_failures'])}")


def run_inference(
    dataset: Dict[str, Any],
    extractor: Any,
    input_dataset_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    samples = dataset.get("samples")
    if not isinstance(samples, list):
        raise ValueError("Dataset must contain a samples list")

    started_at = _utc_now()
    output = copy.deepcopy(dataset)
    original_annotations = copy.deepcopy(
        [
            sample.get("annotation") if isinstance(sample, dict) else None
            for sample in samples
        ]
    )
    quote_lines, paragraphs, line_to_sample = _prepare_extractor_input(
        output["samples"]
    )

    records: List[QuoteRecord] = extractor.extract(
        quote_lines,
        paragraphs=paragraphs,
    )
    failures: Sequence[ExtractionFailure] = list(
        getattr(extractor, "failures", [])
    )
    unmapped_predictions, unmapped_failures = _apply_predictions(
        samples=output["samples"],
        records=records,
        failures=failures,
        line_to_sample=line_to_sample,
    )

    output_annotations = [
        sample.get("annotation") for sample in output["samples"]
    ]
    if output_annotations != original_annotations:
        raise RuntimeError("Inference changed manual annotations; refusing output")

    metadata = output.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Dataset metadata must be an object")
    status_counts: Dict[str, int] = defaultdict(int)
    prediction_count = 0
    for sample in output["samples"]:
        status_counts[str(sample["inference_status"])] += 1
        prediction_count += int(sample["inference_prediction_count"])

    metadata["inference"] = {
        "run_id": str(uuid.uuid4()),
        "started_at": started_at,
        "completed_at": _utc_now(),
        "model": str(getattr(extractor, "model", "unknown")),
        "batch_size": getattr(extractor, "batch_size", None),
        "max_workers": getattr(extractor, "max_workers", None),
        "max_retries": getattr(extractor, "max_retries", None),
        "max_attempts": (
            getattr(extractor, "max_retries", 0) + 1
            if isinstance(getattr(extractor, "max_retries", 0), int)
            else None
        ),
        "request_timeout_seconds": getattr(
            extractor, "request_timeout", None
        ),
        "prompt_sha256": hashlib.sha256(
            SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "input_dataset_sha256": input_dataset_sha256
        or _dataset_digest(dataset),
        "sample_count": len(output["samples"]),
        "prediction_count": prediction_count,
        "failure_count": len(failures),
        "status_counts": dict(sorted(status_counts.items())),
        "unmapped_predictions": unmapped_predictions,
        "unmapped_failures": unmapped_failures,
        "prediction_field": "prediction_snapshot",
        "annotation_preserved": True,
    }
    return output


def _prepare_extractor_input(
    samples: List[Dict[str, Any]],
) -> Tuple[List[QuoteLine], List[ParagraphBlock], Dict[str, int]]:
    quote_lines: List[QuoteLine] = []
    paragraphs: List[ParagraphBlock] = []
    line_to_sample: Dict[str, int] = {}
    paragraph_ids: Dict[Tuple[str, str, str], str] = {}
    seen_sample_ids = set()

    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"samples[{sample_index}] must be an object")
        sample_id = str(sample.get("sample_id") or "")
        if not sample_id:
            raise ValueError(f"samples[{sample_index}] is missing sample_id")
        if sample_id in seen_sample_ids:
            raise ValueError(f"Duplicate sample_id: {sample_id}")
        seen_sample_ids.add(sample_id)

        sample_input = sample.get("input")
        source = sample.get("source") or {}
        if not isinstance(sample_input, dict):
            raise ValueError(f"{sample_id}.input must be an object")
        if not isinstance(source, dict):
            raise ValueError(f"{sample_id}.source must be an object")

        quote_text = sample_input.get("quote_line")
        if not isinstance(quote_text, str) or not quote_text.strip():
            raise ValueError(f"{sample_id}.input.quote_line must be non-empty")
        context = sample_input.get("paragraph_context") or ""
        if not isinstance(context, str):
            raise ValueError(
                f"{sample_id}.input.paragraph_context must be a string"
            )

        source_paragraph_id = source.get("paragraph_id")
        if source_paragraph_id is None:
            source_paragraph_id = f"missing:{sample_id}"
        group_key = (
            str(
                source.get("run_id")
                if source.get("run_id") is not None
                else ""
            ),
            str(source_paragraph_id),
            context,
        )
        paragraph_id = paragraph_ids.get(group_key)
        if paragraph_id is None:
            paragraph_id = "eval_paragraph_{:05d}".format(
                len(paragraph_ids) + 1
            )
            paragraph_ids[group_key] = paragraph_id
            paragraphs.append(
                ParagraphBlock(
                    paragraph_id=paragraph_id,
                    paragraph_type="quote",
                    text=context,
                    quote_time=_optional_string(source.get("quote_time")),
                    quote_source=_optional_string(
                        source.get("quote_source")
                    ),
                    speaker=_optional_string(source.get("speaker")),
                    source_date=_optional_string(source.get("source_date")),
                    confidence=1.0,
                )
            )

        line_id = "eval_line_{:06d}".format(sample_index + 1)
        line_to_sample[line_id] = sample_index
        quote_lines.append(
            QuoteLine(
                line_id=line_id,
                paragraph_id=paragraph_id,
                text=quote_text,
                quote_time=_optional_string(source.get("quote_time")),
                quote_source=_optional_string(source.get("quote_source")),
                speaker=_optional_string(source.get("speaker")),
                source_date=_optional_string(source.get("source_date")),
            )
        )

    return quote_lines, paragraphs, line_to_sample


def _apply_predictions(
    samples: List[Dict[str, Any]],
    records: Sequence[QuoteRecord],
    failures: Sequence[ExtractionFailure],
    line_to_sample: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    predictions_by_sample: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    failures_by_sample: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    unmapped_predictions: List[Dict[str, Any]] = []
    unmapped_failures: List[Dict[str, Any]] = []
    raw_line_to_samples: Dict[str, List[int]] = defaultdict(list)

    for sample_index, sample in enumerate(samples):
        raw_line_to_samples[
            str((sample.get("input") or {}).get("quote_line") or "")
        ].append(sample_index)

    for record in records:
        sample_index = _sample_index_for_record(
            record=record,
            line_to_sample=line_to_sample,
            raw_line_to_samples=raw_line_to_samples,
        )
        serialized = record.to_dict()
        if sample_index is None:
            unmapped_predictions.append(serialized)
            continue
        predictions_by_sample[sample_index].append(serialized)

    for failure in failures:
        sample_index = line_to_sample.get(failure.line_id)
        serialized = failure.to_dict()
        if sample_index is None:
            unmapped_failures.append(serialized)
            continue
        failures_by_sample[sample_index].append(serialized)

    for sample_index, sample in enumerate(samples):
        predictions = predictions_by_sample.get(sample_index, [])
        inference_failures = failures_by_sample.get(sample_index, [])
        sample["prediction_snapshot"] = predictions
        sample["inference_prediction_count"] = len(predictions)
        sample["inference_failure"] = (
            inference_failures if inference_failures else None
        )
        if len(predictions) > 1:
            status = "multiple_predictions"
        elif predictions and inference_failures:
            status = "predicted_with_failure"
        elif predictions:
            status = "predicted"
        elif inference_failures:
            status = "failed"
        else:
            status = "no_prediction"
        sample["inference_status"] = status

    return unmapped_predictions, unmapped_failures


def _sample_index_for_record(
    record: QuoteRecord,
    line_to_sample: Dict[str, int],
    raw_line_to_samples: Dict[str, List[int]],
) -> Optional[int]:
    line_id = _line_id_from_remark(record.remark)
    if line_id is not None:
        return line_to_sample.get(line_id)

    if record.raw_line is not None:
        candidates = raw_line_to_samples.get(record.raw_line, [])
        if len(candidates) == 1:
            return candidates[0]
    return None


def _line_id_from_remark(remark: Optional[str]) -> Optional[str]:
    if not remark:
        return None
    match = LINE_ID_RE.search(remark)
    return match.group(1) if match else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production LLM extractor on an annotated evaluation "
            "dataset without changing its gold annotations."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", help="Override OPENAI_MODEL/MID_MODEL.")
    parser.add_argument(
        "--base-url",
        help="Override OPENAI_BASE_URL for the OpenAI-compatible endpoint.",
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help="Replace an existing output file. The input file is never replaced.",
    )
    return parser.parse_args()


def _validate_paths(
    input_path: Path,
    output_path: Path,
    overwrite_output: bool,
) -> None:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input dataset not found: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output must be different files")
    if output_path.exists() and not overwrite_output:
        raise FileExistsError(
            f"Output already exists: {output_path}; "
            "use --overwrite-output to replace it"
        )


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _dataset_digest(dataset: Dict[str, Any]) -> str:
    canonical = json.dumps(
        dataset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _optional_string(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
