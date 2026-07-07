import argparse
from pathlib import Path

from .exporters import read_messages_json, write_messages_json, write_quotes_json, write_quotes_xlsx
from .splitter import split_messages


DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Bloomberg chat bond quotes to JSON/XLSX.")
    parser.add_argument("input", nargs="?", type=Path, help="Input pasted Bloomberg chat text file.")
    parser.add_argument(
        "--stage",
        choices=["split", "extract", "all"],
        default="all",
        help="Run only split, only LLM extraction, or the full pipeline.",
    )
    parser.add_argument("--source-date", help="Quote date in YYYY-MM-DD. Used to prefix HK quote time.")
    parser.add_argument("--out-dir", type=Path, default=Path("output"), help="Output directory.")
    parser.add_argument("--json-name", default="quotes.json", help="Output JSON file name.")
    parser.add_argument("--xlsx-name", default="quotes.xlsx", help="Output XLSX file name.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model used for extraction.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    parser.add_argument("--batch-size", type=int, default=8, help="Number of chat messages per LLM call.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retries per LLM batch.")
    parser.add_argument("--no-progress", action="store_true", help="Disable LLM extraction progress output.")
    parser.add_argument(
        "--messages-input",
        type=Path,
        help="Use an existing messages.json for --stage extract.",
    )
    parser.add_argument(
        "--messages-name",
        default="messages.json",
        help="Debug JSON file containing split Bloomberg message blocks.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    json_path = args.out_dir / args.json_name
    xlsx_path = args.out_dir / args.xlsx_name
    messages_path = args.out_dir / args.messages_name

    if args.stage in {"split", "all"}:
        if args.input is None:
            parser.error("input is required for --stage split/all")
        raw_text = args.input.read_text(encoding="utf-8")
        messages = split_messages(raw_text, source_date=args.source_date)
        write_messages_json(messages, messages_path)
        print(f"messages={len(messages)}")
        print(f"messages_json={messages_path}")

        if args.stage == "split":
            return
    else:
        source_messages_path = args.messages_input or args.input or messages_path
        messages = read_messages_json(source_messages_path)
        print(f"messages={len(messages)}")
        print(f"messages_json={source_messages_path}")

    from .llm_extractor import OpenAIQuoteExtractor

    extractor = OpenAIQuoteExtractor(
        model=args.model,
        batch_size=args.batch_size,
        max_retries=args.max_retries,
        show_progress=not args.no_progress,
        base_url=args.base_url,
    )
    records = extractor.extract(messages)

    write_quotes_json(records, json_path)
    write_quotes_xlsx(records, xlsx_path)

    print(f"quotes={len(records)}")
    print(f"json={json_path}")
    print(f"xlsx={xlsx_path}")


if __name__ == "__main__":
    main()
