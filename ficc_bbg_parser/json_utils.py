import json
from typing import Any, Dict


def load_json_object(content: str) -> Dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = _strip_code_fence(text)
    return json.loads(text)


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
