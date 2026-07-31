import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


CRITICAL_FIELDS = [
    "ticker",
    "currency",
    "maturity_date",
    "coupon_type",
    "coupon_rate",
]
EVALUATION_SCHEMA_VERSION = "1.1"
VALID_STATUSES = {"pending", "completed"}
VALID_AVAILABILITY = {
    "pending",
    "present",
    "inferable",
    "absent",
    "ambiguous",
}
VALID_DIFFICULTIES = {
    "easy",
    "hard_extraction",
    "hard_context",
    "unanswerable",
    "ambiguous",
}
_PROCESS_LOCK = threading.RLock()


class EvaluationDatasetStore:
    def __init__(self, input_path: Path, output_path: Path) -> None:
        self.input_path = input_path.expanduser().resolve()
        self.output_path = output_path.expanduser().resolve()
        if self.input_path == self.output_path:
            raise ValueError("Evaluation input and output paths must be different")

    def load(self) -> Dict[str, Any]:
        with self._locked():
            dataset, active_path = self._load_unlocked()
        return {
            "dataset": dataset,
            "source_file": str(self.input_path),
            "working_file": str(self.output_path),
            "loaded_from": "working" if active_path == self.output_path else "source",
            "progress": annotation_progress(dataset),
        }

    def save_annotation(
        self,
        sample_id: str,
        annotation: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized = validate_annotation(annotation)
        with self._locked():
            dataset, _ = self._load_unlocked()
            samples = dataset.get("samples")
            if not isinstance(samples, list):
                raise ValueError("Evaluation dataset must contain a samples list")

            target = next(
                (
                    sample
                    for sample in samples
                    if isinstance(sample, dict)
                    and str(sample.get("sample_id")) == sample_id
                ),
                None,
            )
            if target is None:
                raise KeyError(f"Evaluation sample not found: {sample_id}")

            target["annotation"] = normalized
            metadata = dataset.setdefault("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("Evaluation dataset metadata must be an object")
            metadata["annotation_updated_at"] = _utc_now()
            metadata["annotation_output_file"] = self.output_path.name
            self._write_unlocked(dataset)

        return {
            "sample": target,
            "working_file": str(self.output_path),
            "progress": annotation_progress(dataset),
        }

    def active_path(self) -> Path:
        with self._locked():
            _, active_path = self._load_unlocked()
        return active_path

    def _load_unlocked(self) -> Any:
        active_path = (
            self.output_path if self.output_path.is_file() else self.input_path
        )
        if not active_path.is_file():
            raise FileNotFoundError(
                f"Evaluation dataset not found: {self.input_path}"
            )
        dataset = json.loads(active_path.read_text(encoding="utf-8"))
        if not isinstance(dataset, dict):
            raise ValueError("Evaluation dataset root must be an object")
        migrate_evaluation_dataset(dataset)
        return dataset, active_path

    def _write_unlocked(self, dataset: Dict[str, Any]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.output_path.parent),
                prefix=f".{self.output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = handle.name
                json.dump(dataset, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.output_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_key = hashlib.sha256(
            str(self.output_path).encode("utf-8")
        ).hexdigest()[:20]
        lock_path = Path(tempfile.gettempdir()) / (
            f"ficc_bbg_eval_{lock_key}.lock"
        )
        with _PROCESS_LOCK:
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                try:
                    import fcntl
                except ImportError:
                    fcntl = None
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def validate_annotation(annotation: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(annotation, dict):
        raise ValueError("annotation must be an object")

    status = annotation.get("status")
    if status not in VALID_STATUSES:
        raise ValueError("annotation.status must be pending or completed")

    difficulty = annotation.get("difficulty_gold")
    if difficulty is not None and difficulty not in VALID_DIFFICULTIES:
        raise ValueError("annotation.difficulty_gold is invalid")

    availability = annotation.get("field_availability")
    gold = annotation.get("gold")
    if not isinstance(availability, dict):
        raise ValueError("annotation.field_availability must be an object")
    if not isinstance(gold, dict):
        raise ValueError("annotation.gold must be an object")

    normalized_availability: Dict[str, str] = {}
    normalized_gold: Dict[str, Any] = {}
    for field in CRITICAL_FIELDS:
        field_availability = availability.get(field)
        if field_availability not in VALID_AVAILABILITY:
            raise ValueError(
                f"{field} availability must be present, inferable, absent, "
                "ambiguous, or pending"
            )
        if status == "completed" and field_availability == "pending":
            raise ValueError(
                f"{field} availability cannot be pending when completed"
            )

        value = _normalize_gold_value(field, gold.get(field))
        if field_availability in {"present", "inferable"} and _is_blank(value):
            if status == "completed":
                raise ValueError(
                    f"{field} requires a gold value when {field_availability}"
                )
        if field_availability == "absent" and not _is_blank(value):
            raise ValueError(f"{field} gold must be null when absent")

        normalized_availability[field] = field_availability
        normalized_gold[field] = value

    return {
        "status": status,
        "difficulty_gold": difficulty,
        "field_availability": normalized_availability,
        "gold": normalized_gold,
        "annotator": _optional_text(annotation.get("annotator")),
        "remark": _optional_text(annotation.get("remark")),
    }


def annotation_progress(dataset: Dict[str, Any]) -> Dict[str, int]:
    samples = dataset.get("samples")
    if not isinstance(samples, list):
        raise ValueError("Evaluation dataset must contain a samples list")
    completed = sum(
        1
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("annotation"), dict)
        and sample["annotation"].get("status") == "completed"
    )
    return {
        "total": len(samples),
        "completed": completed,
        "pending": len(samples) - completed,
    }


def migrate_evaluation_dataset(dataset: Dict[str, Any]) -> bool:
    samples = dataset.get("samples")
    if not isinstance(samples, list):
        raise ValueError("Evaluation dataset must contain a samples list")

    changed = dataset.get("schema_version") != EVALUATION_SCHEMA_VERSION
    dataset["schema_version"] = EVALUATION_SCHEMA_VERSION
    metadata = dataset.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Evaluation dataset metadata must be an object")
    if metadata.get("critical_fields") != CRITICAL_FIELDS:
        metadata["critical_fields"] = list(CRITICAL_FIELDS)
        changed = True
    if metadata.get("critical_field_count") != len(CRITICAL_FIELDS):
        metadata["critical_field_count"] = len(CRITICAL_FIELDS)
        changed = True
    issuer_policy = (
        "issuer is optional metadata and is excluded from extraction accuracy"
    )
    if metadata.get("issuer_evaluation_policy") != issuer_policy:
        metadata["issuer_evaluation_policy"] = issuer_policy
        changed = True

    difficulty_counts: Dict[str, int] = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        missing_fields = sample.get("missing_fields_current")
        if isinstance(missing_fields, list) and "issuer" in missing_fields:
            sample["missing_fields_current"] = [
                field for field in missing_fields if field != "issuer"
            ]
            changed = True
        current_missing = sample.get("missing_fields_current")
        if isinstance(current_missing, list):
            missing_count = len(current_missing)
            if sample.get("missing_field_count") != missing_count:
                sample["missing_field_count"] = missing_count
                changed = True
            if sample.get("difficulty_candidate") != "extraction_failure":
                difficulty = _difficulty_bucket(missing_count)
                if sample.get("difficulty_candidate") != difficulty:
                    sample["difficulty_candidate"] = difficulty
                    changed = True

        annotation = sample.get("annotation")
        if isinstance(annotation, dict):
            availability = annotation.get("field_availability")
            gold = annotation.get("gold")
            if isinstance(availability, dict) and "issuer" in availability:
                del availability["issuer"]
                changed = True
            if isinstance(gold, dict) and "issuer" in gold:
                del gold["issuer"]
                changed = True

        difficulty = str(sample.get("difficulty_candidate") or "unknown")
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1

    actual_distribution = dict(sorted(difficulty_counts.items()))
    if metadata.get("actual_difficulty_distribution") != actual_distribution:
        metadata["actual_difficulty_distribution"] = actual_distribution
        changed = True
    return changed


def _normalize_gold_value(field: str, value: Any) -> Any:
    if _is_blank(value):
        return None
    if field == "coupon_rate":
        if isinstance(value, bool):
            raise ValueError("coupon_rate must be a number or null")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("coupon_rate must be a number or null") from exc
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return value.strip()


def _difficulty_bucket(missing_count: int) -> str:
    if missing_count == 0:
        return "easy"
    if missing_count == 1:
        return "missing_1"
    if missing_count <= 3:
        return "missing_2_3"
    return "missing_4_plus"


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
