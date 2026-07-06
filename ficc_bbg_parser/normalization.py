def clean_text(value: str) -> str:
    return (
        value.replace("\u00a0", " ")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .replace(">>", " >> ")
    )


def extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.strip("* ").strip()
    return ""
