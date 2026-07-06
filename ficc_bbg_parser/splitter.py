import re
from typing import List, Optional

from .models import MessageBlock
from .normalization import clean_text


MESSAGE_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<speaker>[^:]+?)\s*:\s*(?P<body>.*)$")


def split_messages(raw_text: str, source_date: Optional[str] = None) -> List[MessageBlock]:
    messages: List[MessageBlock] = []
    current = None
    current_lines = []

    for raw_line in clean_text(raw_text).splitlines():
        match = MESSAGE_RE.match(raw_line)
        if match:
            if current is not None:
                messages.append(
                    MessageBlock(
                        message_id=f"msg_{len(messages) + 1:05d}",
                        message_time=current["time"],
                        speaker=current["speaker"].strip(),
                        text="\n".join(current_lines).strip(),
                        source_date=source_date,
                    )
                )
            current = {
                "time": match.group("time"),
                "speaker": match.group("speaker"),
            }
            current_lines = [match.group("body")]
            continue

        if current is not None:
            current_lines.append(raw_line)

    if current is not None:
        messages.append(
            MessageBlock(
                message_id=f"msg_{len(messages) + 1:05d}",
                message_time=current["time"],
                speaker=current["speaker"].strip(),
                text="\n".join(current_lines).strip(),
                source_date=source_date,
            )
        )

    return messages
