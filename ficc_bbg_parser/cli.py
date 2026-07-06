import argparse
from pathlib import Path

from .exporters import write_messages_json, write_quotes_json, write_quotes_xlsx
from .llm_extractor import DEFAULT_MODEL, OpenAIQuoteExtractor
from .pipeline import parse_chat_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Bloomberg chat bond quotes to JSON/XLSX.")
    parser.add_argument("input", type=Path, help="Input pasted Bloomberg chat text file.")
    parser.add_argument("--source-date", help="Quote date in YYYY-MM-DD. Used to prefix HK quote time.")
    parser.add_argument("--out-dir", type=Path, default=Path("output"), help="Output directory.")
    parser.add_argument("--json-name", default="quotes.json", help="Output JSON file name.")
    parser.add_argument("--xlsx-name", default="quotes.xlsx", help="Output XLSX file name.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model used for extraction.")
    parser.add_argument("--batch-size", type=int, default=8, help="Number of chat messages per LLM call.")
    parser.add_argument(
        "--messages-name",
        default="messages.json",
        help="Debug JSON file containing split Bloomberg message blocks.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raw_text = args.input.read_text(encoding="utf-8")
    extractor = OpenAIQuoteExtractor(model=args.model, batch_size=args.batch_size)

    messages, records = parse_chat_text(raw_text, source_date=args.source_date, extractor=extractor)

    json_path = args.out_dir / args.json_name
    xlsx_path = args.out_dir / args.xlsx_name
    messages_path = args.out_dir / args.messages_name

    write_quotes_json(records, json_path)
    write_quotes_xlsx(records, xlsx_path)
    write_messages_json(messages, messages_path)

    print(f"messages={len(messages)}")
    print(f"quotes={len(records)}")
    print(f"json={json_path}")
    print(f"xlsx={xlsx_path}")
    print(f"messages_json={messages_path}")


if __name__ == "__main__":
    main()
