import json
from pathlib import Path
from typing import Iterable, List

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import ExtractionFailure, EXPORT_COLUMNS, ParagraphBlock, QuoteLine, QuoteRecord


def write_quotes_json(records: Iterable[QuoteRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.to_dict() for record in records]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_extraction_failures_json(
    failures: Iterable[ExtractionFailure],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [failure.to_dict() for failure in failures]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_paragraphs_json(paragraphs: Iterable[ParagraphBlock], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "paragraph_id": paragraph.paragraph_id,
            "paragraph_type": paragraph.paragraph_type,
            "text": paragraph.text,
            "quote_time": paragraph.quote_time,
            "quote_source": paragraph.quote_source,
            "speaker": paragraph.speaker,
            "source_date": paragraph.source_date,
            "confidence": paragraph.confidence,
        }
        for paragraph in paragraphs
    ]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_paragraphs_json(input_path: Path) -> List[ParagraphBlock]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return [
        ParagraphBlock(
            paragraph_id=item["paragraph_id"],
            paragraph_type=item["paragraph_type"],
            text=item["text"],
            quote_time=item.get("quote_time"),
            quote_source=item.get("quote_source"),
            speaker=item.get("speaker"),
            source_date=item.get("source_date"),
            confidence=float(item.get("confidence") or 0),
        )
        for item in payload
    ]


def write_quote_lines_json(quote_lines: Iterable[QuoteLine], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "line_id": quote_line.line_id,
            "paragraph_id": quote_line.paragraph_id,
            "text": quote_line.text,
            "quote_time": quote_line.quote_time,
            "quote_source": quote_line.quote_source,
            "speaker": quote_line.speaker,
            "source_date": quote_line.source_date,
        }
        for quote_line in quote_lines
    ]
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_quote_lines_json(input_path: Path) -> List[QuoteLine]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    return [
        QuoteLine(
            line_id=item["line_id"],
            paragraph_id=item["paragraph_id"],
            text=item["text"],
            quote_time=item.get("quote_time"),
            quote_source=item.get("quote_source"),
            speaker=item.get("speaker"),
            source_date=item.get("source_date"),
        )
        for item in payload
    ]


def write_quote_lines_txt(quote_lines: Iterable[QuoteLine], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(line.text for line in quote_lines), encoding="utf-8")


def read_quote_lines_txt(input_path: Path) -> List[QuoteLine]:
    quote_lines: List[QuoteLine] = []
    for idx, raw_line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw_line.strip()
        if not text:
            continue
        quote_lines.append(
            QuoteLine(
                line_id=f"line_{len(quote_lines) + 1:05d}",
                paragraph_id="manual_input",
                text=text,
            )
        )
    return quote_lines


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
