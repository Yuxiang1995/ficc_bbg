import argparse
from pathlib import Path

from .exporters import (
    read_paragraphs_json,
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
    parser = argparse.ArgumentParser(
        description="Parse Bloomberg chat bond quotes to PostgreSQL with optional debug files."
    )
    parser.add_argument("input", nargs="?", type=Path, help="Input pasted Bloomberg chat text file.")
    parser.add_argument(
        "--input-source",
        choices=["txt", "sqlserver"],
        default="txt",
        help="Read raw text from a debug txt file or SQL Server.",
    )
    parser.add_argument(
        "--stage",
        choices=["split", "extract", "all"],
        default="all",
        help="Run only split, only LLM extraction, or the full pipeline.",
    )
    parser.add_argument("--source-date", help="Quote date in YYYY-MM-DD. Used to prefix HK quote time.")
    parser.add_argument("--receive-date", help="SQL Server RECEIVE_DATE day in YYYY-MM-DD.")
    parser.add_argument("--out-dir", type=Path, default=Path("output"), help="Output directory.")
    parser.add_argument("--json-name", default="quotes.json", help="Output JSON file name.")
    parser.add_argument("--xlsx-name", default="quotes.xlsx", help="Output XLSX file name.")
    parser.add_argument("--paragraphs-name", default="paragraphs.json", help="LLM split paragraph JSON file name.")
    parser.add_argument("--quote-lines-name", default="quote_lines.txt", help="Newline-delimited quote line file name.")
    parser.add_argument("--quote-lines-json-name", default="quote_lines.json", help="Quote line metadata JSON file name.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum quote lines from one paragraph per extractor LLM call.",
    )
    parser.add_argument("--extract-workers", type=int, default=4, help="Parallel LLM requests for quote extraction.")
    parser.add_argument("--message-batch-max-chars", type=int, default=16000, help="Target max payload chars per splitter LLM call.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retries per LLM batch.")
    parser.add_argument("--split-timeout", type=float, default=180, help="HTTP timeout seconds for the splitter LLM request.")
    parser.add_argument("--extract-timeout", type=float, default=90, help="HTTP timeout seconds for each extractor LLM request.")
    parser.add_argument("--no-progress", action="store_true", help="Disable LLM extraction progress output.")
    parser.add_argument(
        "--output-mode",
        choices=["postgres", "debug", "both"],
        default="postgres",
        help="Write PostgreSQL by default, debug files only, or both.",
    )
    parser.add_argument(
        "--paragraphs-input",
        type=Path,
        help="Use an existing paragraphs.json as extractor context for --stage extract.",
    )
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
    source_date = args.source_date or args.receive_date
    source_emails = []
    splitter_model = None
    write_debug = args.output_mode in {"debug", "both"}

    if args.stage in {"split", "all"}:
        from .llm_splitter import OpenAIParagraphSplitter

        raw_text, source_emails = _read_input(args, parser)
        splitter = OpenAIParagraphSplitter(
            message_batch_max_chars=args.message_batch_max_chars,
            max_retries=args.max_retries,
            request_timeout=args.split_timeout,
            show_progress=not args.no_progress,
            base_url=args.base_url,
        )
        splitter_model = splitter.model
        paragraphs, quote_lines = splitter.split(raw_text, source_date=source_date)
        if args.stage == "split" or write_debug:
            write_paragraphs_json(paragraphs, paragraphs_path)
            write_quote_lines_json(quote_lines, quote_lines_json_path)
            write_quote_lines_txt(quote_lines, quote_lines_path)
        print(f"paragraphs={len(paragraphs)}")
        print(f"quote_lines={len(quote_lines)}")
        if args.stage == "split" or write_debug:
            print(f"paragraphs_json={paragraphs_path}")
            print(f"quote_lines_json={quote_lines_json_path}")
            print(f"quote_lines_txt={quote_lines_path}")

        if args.stage == "split":
            return
    else:
        source_quote_lines_path = args.quote_lines_input or args.input or quote_lines_json_path
        quote_lines = _read_quote_lines(source_quote_lines_path)
        source_paragraphs_path = args.paragraphs_input or paragraphs_path
        paragraphs = read_paragraphs_json(source_paragraphs_path) if source_paragraphs_path.exists() else []
        print(f"quote_lines={len(quote_lines)}")
        print(f"quote_lines_input={source_quote_lines_path}")
        if paragraphs:
            print(f"paragraphs={len(paragraphs)}")
            print(f"paragraphs_input={source_paragraphs_path}")

    from .llm_extractor import OpenAIQuoteExtractor

    extractor = OpenAIQuoteExtractor(
        batch_size=args.batch_size,
        max_workers=args.extract_workers,
        max_retries=args.max_retries,
        request_timeout=args.extract_timeout,
        show_progress=not args.no_progress,
        base_url=args.base_url,
    )
    records = extractor.extract(quote_lines, paragraphs=paragraphs)

    if write_debug:
        write_quotes_json(records, json_path)
        write_quotes_xlsx(records, xlsx_path)

    if args.output_mode in {"postgres", "both"}:
        from .pg_store import PostgresQuoteStore

        store_result = PostgresQuoteStore().save_run(
            source_mode=args.input_source if args.stage == "all" else "manual",
            source_date=source_date,
            emails=source_emails,
            paragraphs=paragraphs,
            quote_lines=quote_lines,
            records=records,
            splitter_model=splitter_model,
            extractor_model=extractor.model,
            metadata={"stage": args.stage},
        )
        print(f"postgres_run_id={store_result.run_id}")
        print(f"postgres_quotes={store_result.quote_count}")

    print(f"quotes={len(records)}")
    if write_debug:
        print(f"json={json_path}")
        print(f"xlsx={xlsx_path}")


def _read_quote_lines(path: Path):
    if path.suffix.lower() == ".json":
        return read_quote_lines_json(path)
    return read_quote_lines_txt(path)


def _read_input(args, parser):
    if args.input_source == "txt":
        if args.input is None:
            parser.error("input is required when --input-source txt")
        raw_text = args.input.read_text(encoding="utf-8")
        return raw_text, [
            {
                "source_system": "txt",
                "source_record_key": str(args.input.resolve()),
                "subject": args.input.name,
                "content": raw_text,
                "receive_date": args.source_date,
            }
        ]

    receive_date = args.receive_date or args.source_date
    if not receive_date:
        parser.error("--receive-date or --source-date is required when --input-source sqlserver")

    from .db_source import fetch_chat_email_rows

    rows = fetch_chat_email_rows(receive_date)
    raw_text = "\n\n".join(row["content"] for row in rows if row["content"])
    if not raw_text.strip():
        raise ValueError(f"No CONTENT found for RECEIVE_DATE day {receive_date}")
    for row in rows:
        row["source_system"] = "sqlserver"
    return raw_text, rows


if __name__ == "__main__":
    main()
