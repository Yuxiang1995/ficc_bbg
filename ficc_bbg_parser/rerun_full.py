import argparse
import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_ROOT = Path("output/full_rerun")


def main() -> int:
    args = _parse_args()
    source_dates = fetch_source_dates(args.start_date, args.end_date)
    if not source_dates:
        print(
            "No SQL Server rows found from {} through {}; PostgreSQL was not changed.".format(
                args.start_date.isoformat(),
                args.end_date.isoformat(),
            )
        )
        return 0

    _print_plan(args, source_dates)
    if args.dry_run:
        for source_date in source_dates:
            print("DRY RUN: {}".format(_format_command(build_cli_command(args, source_date))))
        return 0

    _confirm(args, source_dates)
    if not args.skip_schema_init:
        _run_checked([sys.executable, "-m", "ficc_bbg_parser.pg_schema"])

    deleted_runs = 0
    if not args.skip_clear:
        deleted_runs = clear_postgres_runs(
            start_date=args.start_date,
            end_date=args.end_date,
            clear_scope=args.clear_scope,
        )
        print("postgres_deleted_runs={}".format(deleted_runs))

    summary: Dict[str, Any] = {
        "started_at": _utc_now(),
        "completed_at": None,
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "clear_scope": None if args.skip_clear else args.clear_scope,
        "deleted_runs": deleted_runs,
        "source_dates": [item.isoformat() for item in source_dates],
        "completed_dates": [],
        "failed_dates": [],
    }
    summary_path = args.out_root / "rerun_summary.json"

    for index, source_date in enumerate(source_dates, start=1):
        print(
            "\n=== [{}/{}] processing {} ===".format(
                index,
                len(source_dates),
                source_date.isoformat(),
            )
        )
        command = build_cli_command(args, source_date)
        result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
        if result.returncode == 0:
            summary["completed_dates"].append(source_date.isoformat())
        else:
            summary["failed_dates"].append(
                {
                    "date": source_date.isoformat(),
                    "returncode": result.returncode,
                    "command": command,
                }
            )
        summary["completed_at"] = _utc_now()
        _write_summary(summary_path, summary)

        if result.returncode != 0 and not args.continue_on_error:
            print(
                "Stopped after failure on {}. Resume with --start-date {} "
                "--end-date {} --skip-clear.".format(
                    source_date.isoformat(),
                    source_date.isoformat(),
                    args.end_date.isoformat(),
                ),
                file=sys.stderr,
            )
            print("summary={}".format(summary_path))
            return result.returncode

    summary["completed_at"] = _utc_now()
    _write_summary(summary_path, summary)
    print("\ncompleted_dates={}".format(len(summary["completed_dates"])))
    print("failed_dates={}".format(len(summary["failed_dates"])))
    print("summary={}".format(summary_path))
    return 1 if summary["failed_dates"] else 0


def fetch_source_dates(start_date: date, end_date: date) -> List[date]:
    from .db_source import _import_pymssql, _required

    pymssql = _import_pymssql()
    end_exclusive = end_date + timedelta(days=1)
    query = """
SELECT DISTINCT CAST(RECEIVE_DATE AS date) AS RECEIVE_DAY
FROM {table}
WHERE RECEIVE_DATE >= %s AND RECEIVE_DATE < %s
  AND CONTENT IS NOT NULL
  AND DATALENGTH(CONTENT) > 0
ORDER BY RECEIVE_DAY
""".format(table=settings.DB_TABLE)

    with pymssql.connect(
        server=settings.DB_SERVER,
        port=settings.DB_PORT,
        user=_required("DB_USERNAME", settings.DB_USERNAME),
        password=_required("DB_PASSWORD", settings.DB_PASSWORD),
        database=settings.DB_DATABASE,
        charset="utf8",
        as_dict=True,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute(
            query,
            (
                datetime.combine(start_date, time.min),
                datetime.combine(end_exclusive, time.min),
            ),
        )
        result = []
        for row in cursor.fetchall():
            value = row.get("RECEIVE_DAY")
            if isinstance(value, datetime):
                value = value.date()
            elif not isinstance(value, date):
                value = date.fromisoformat(str(value)[:10])
            result.append(value)
    return result


def clear_postgres_runs(
    start_date: date,
    end_date: date,
    clear_scope: str,
) -> int:
    try:
        import psycopg
    except ImportError as exc:
        raise ImportError(
            "psycopg is required for PostgreSQL. Install requirements.txt."
        ) from exc

    if not settings.POSTGRES_PASSWORD:
        raise ValueError("Missing required PostgreSQL env var: POSTGRES_PASSWORD")

    schema = psycopg.sql.Identifier(settings.POSTGRES_SCHEMA)
    run_table = psycopg.sql.SQL("{}.ingestion_run").format(schema)
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DATABASE,
        user=settings.POSTGRES_USERNAME,
        password=settings.POSTGRES_PASSWORD,
        sslmode=settings.POSTGRES_SSLMODE,
    ) as connection:
        with connection.cursor() as cursor:
            if clear_scope == "all":
                cursor.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM {}").format(run_table)
                )
                deleted_runs = int(cursor.fetchone()[0])
                cursor.execute(
                    psycopg.sql.SQL("TRUNCATE TABLE {} CASCADE").format(run_table)
                )
                return deleted_runs

            cursor.execute(
                psycopg.sql.SQL(
                    "SELECT count(*) FROM {} "
                    "WHERE source_mode = 'sqlserver' "
                    "AND source_date BETWEEN %s AND %s"
                ).format(run_table),
                (start_date, end_date),
            )
            deleted_runs = int(cursor.fetchone()[0])
            cursor.execute(
                psycopg.sql.SQL(
                    "DELETE FROM {} "
                    "WHERE source_mode = 'sqlserver' "
                    "AND source_date BETWEEN %s AND %s"
                ).format(run_table),
                (start_date, end_date),
            )
            return deleted_runs


def build_cli_command(args: argparse.Namespace, source_date: date) -> List[str]:
    command = [
        sys.executable,
        "-m",
        "ficc_bbg_parser.cli",
        "--input-source",
        "sqlserver",
        "--receive-date",
        source_date.isoformat(),
        "--stage",
        "all",
        "--output-mode",
        args.output_mode,
        "--extract-workers",
        str(args.extract_workers),
        "--batch-size",
        str(args.batch_size),
        "--message-batch-max-chars",
        str(args.message_batch_max_chars),
        "--max-retries",
        str(args.max_retries),
        "--split-timeout",
        str(args.split_timeout),
        "--extract-timeout",
        str(args.extract_timeout),
    ]
    if args.output_mode == "both":
        command.extend(
            ["--out-dir", str(args.out_root / source_date.isoformat())]
        )
    if args.no_progress:
        command.append("--no-progress")
    return command


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete old PostgreSQL parser runs and reprocess all SQL Server "
            "email dates in an inclusive date range."
        )
    )
    parser.add_argument("--start-date", required=True, type=_iso_date)
    parser.add_argument("--end-date", required=True, type=_iso_date)
    parser.add_argument(
        "--clear-scope",
        choices=["range", "all"],
        default="range",
        help=(
            "Delete sqlserver runs in the selected range, or truncate every "
            "ingestion run. Default: range."
        ),
    )
    parser.add_argument(
        "--skip-clear",
        action="store_true",
        help="Do not delete PostgreSQL data. Use this when resuming after a failure.",
    )
    parser.add_argument(
        "--skip-schema-init",
        action="store_true",
        help="Skip running ficc_bbg_parser.pg_schema before clearing data.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with later source dates after a failed day.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the destructive-action prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Show source dates and CLI commands without writing PostgreSQL.")
    parser.add_argument(
        "--output-mode",
        choices=["postgres", "both"],
        default="postgres",
    )
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--extract-workers", type=_positive_int, default=4)
    parser.add_argument("--batch-size", type=_positive_int, default=10)
    parser.add_argument("--message-batch-max-chars", type=_positive_int, default=16000)
    parser.add_argument("--max-retries", type=_non_negative_int, default=2)
    parser.add_argument("--split-timeout", type=_positive_float, default=180.0)
    parser.add_argument("--extract-timeout", type=_positive_float, default=90.0)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)
    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date")
    return args


def _print_plan(args: argparse.Namespace, source_dates: List[date]) -> None:
    print("range={}..{}".format(args.start_date, args.end_date))
    print("sqlserver_dates={}".format(len(source_dates)))
    print("first_source_date={}".format(source_dates[0]))
    print("last_source_date={}".format(source_dates[-1]))
    print("postgres_clear={}".format("none" if args.skip_clear else args.clear_scope))
    print("output_mode={}".format(args.output_mode))


def _confirm(args: argparse.Namespace, source_dates: List[date]) -> None:
    if args.yes or args.skip_clear:
        return
    if not sys.stdin.isatty():
        raise RuntimeError("Interactive confirmation is unavailable; pass --yes to continue")
    prompt = (
        "This will clear PostgreSQL scope '{}' and process {} source dates. "
        "Type YES to continue: "
    ).format(args.clear_scope, len(source_dates))
    if input(prompt).strip() != "YES":
        raise SystemExit("Cancelled; PostgreSQL was not changed")


def _run_checked(command: List[str]) -> None:
    print("running={}".format(_format_command(command)))
    subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)


def _write_summary(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _format_command(command: List[str]) -> str:
    return " ".join(command)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or greater")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
