import argparse
from pathlib import Path

from .exporters import (
    read_quote_lines_json,
    read_quote_lines_txt,
    write_paragraphs_json,
    write_quote_lines_json,
    write_quote_lines_txt,
    write_quotes_json,
    write_quotes_xlsx,
)
from .settings import DEFAULT_BASE_URL


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
    parser.add_argument("--paragraphs-name", default="paragraphs.json", help="LLM split paragraph JSON file name.")
    parser.add_argument("--quote-lines-name", default="quote_lines.txt", help="Newline-delimited quote line file name.")
    parser.add_argument("--quote-lines-json-name", default="quote_lines.json", help="Quote line metadata JSON file name.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    parser.add_argument("--batch-size", type=int, default=20, help="Number of quote lines per extractor LLM call.")
    parser.add_argument("--extract-workers", type=int, default=1, help="Parallel LLM requests for quote extraction.")
    parser.add_argument("--message-batch-size", type=int, default=10, help="Number of raw messages per splitter LLM call.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retries per LLM batch.")
    parser.add_argument("--split-timeout", type=float, default=180, help="HTTP timeout seconds for the splitter LLM request.")
    parser.add_argument("--extract-timeout", type=float, default=30, help="HTTP timeout seconds for each extractor LLM request.")
    parser.add_argument("--no-progress", action="store_true", help="Disable LLM extraction progress output.")
    parser.add_argument(
        "--quote-lines-input",
        type=Path,
        help="Use an existing quote_lines.json or quote_lines.txt for --stage extract.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    json_path = args.out_dir / args.json_name
    xlsx_path = args.out_dir / args.xlsx_name
    paragraphs_path = args.out_dir / args.paragraphs_name
    quote_lines_path = args.out_dir / args.quote_lines_name
    quote_lines_json_path = args.out_dir / args.quote_lines_json_name

    if args.stage in {"split", "all"}:
        if args.input is None:
            parser.error("input is required for --stage split/all")
        from .llm_splitter import OpenAIParagraphSplitter

        raw_text = args.input.read_text(encoding="utf-8")
        splitter = OpenAIParagraphSplitter(
            message_batch_size=args.message_batch_size,
            max_retries=args.max_retries,
            request_timeout=args.split_timeout,
            show_progress=not args.no_progress,
            base_url=args.base_url,
        )
        paragraphs, quote_lines = splitter.split(raw_text, source_date=args.source_date)
        write_paragraphs_json(paragraphs, paragraphs_path)
        write_quote_lines_json(quote_lines, quote_lines_json_path)
        write_quote_lines_txt(quote_lines, quote_lines_path)
        print(f"paragraphs={len(paragraphs)}")
        print(f"quote_lines={len(quote_lines)}")
        print(f"paragraphs_json={paragraphs_path}")
        print(f"quote_lines_json={quote_lines_json_path}")
        print(f"quote_lines_txt={quote_lines_path}")

        if args.stage == "split":
            return
    else:
        source_quote_lines_path = args.quote_lines_input or args.input or quote_lines_json_path
        quote_lines = _read_quote_lines(source_quote_lines_path)
        print(f"quote_lines={len(quote_lines)}")
        print(f"quote_lines_input={source_quote_lines_path}")

    from .llm_extractor import OpenAIQuoteExtractor

    extractor = OpenAIQuoteExtractor(
        batch_size=args.batch_size,
        max_workers=args.extract_workers,
        max_retries=args.max_retries,
        request_timeout=args.extract_timeout,
        show_progress=not args.no_progress,
        base_url=args.base_url,
    )
    records = extractor.extract(quote_lines)

    write_quotes_json(records, json_path)
    write_quotes_xlsx(records, xlsx_path)

    print(f"quotes={len(records)}")
    print(f"json={json_path}")
    print(f"xlsx={xlsx_path}")


def _read_quote_lines(path: Path):
    if path.suffix.lower() == ".json":
        return read_quote_lines_json(path)
    return read_quote_lines_txt(path)


if __name__ == "__main__":
    main()
