import json
from pathlib import Path
from typing import Iterable, List

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import EXPORT_COLUMNS, MessageBlock, QuoteRecord


def write_quotes_json(records: Iterable[QuoteRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.to_dict() for record in records]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_messages_json(messages: Iterable[MessageBlock], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "message_id": message.message_id,
            "message_time": message.message_time,
            "speaker": message.speaker,
            "source_date": message.source_date,
            "text": message.text,
        }
        for message in messages
    ]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_quotes_xlsx(records: List[QuoteRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "quotes"
    sheet.append(EXPORT_COLUMNS)

    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font

    for record in records:
        row = record.to_dict()
        sheet.append([row.get(column) for column in EXPORT_COLUMNS])

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    _autosize_columns(sheet)

    workbook.save(output_path)


def _autosize_columns(sheet) -> None:
    for column_cells in sheet.columns:
        max_length = 0
        column_index = column_cells[0].column
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(value))
        width = min(max(max_length + 2, 10), 60)
        sheet.column_dimensions[get_column_letter(column_index)].width = width
