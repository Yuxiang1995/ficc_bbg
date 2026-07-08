from datetime import datetime, timedelta
from typing import List

from . import settings


def fetch_chat_email_content(receive_date: str) -> str:
    rows = fetch_chat_email_rows(receive_date)
    return "\n\n".join(row["content"] for row in rows if row["content"])


def fetch_chat_email_rows(receive_date: str) -> List[dict]:
    pymssql = _import_pymssql()
    start_dt = _parse_date(receive_date)
    end_dt = start_dt + timedelta(days=1)
    sql = f"""
SELECT SUBJECT, CONTENT, RECEIVE_DATE
FROM {settings.DB_TABLE}
WHERE RECEIVE_DATE >= %s AND RECEIVE_DATE < %s
ORDER BY RECEIVE_DATE
"""

    with pymssql.connect(
        server=settings.DB_SERVER,
        port=settings.DB_PORT,
        user=_required("DB_USERNAME", settings.DB_USERNAME),
        password=_required("DB_PASSWORD", settings.DB_PASSWORD),
        database=settings.DB_DATABASE,
        charset="utf8",
        as_dict=True,
    ) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (start_dt, end_dt))
        rows = []
        for row in cursor.fetchall():
            rows.append(
                {
                    "subject": row.get("SUBJECT"),
                    "content": row.get("CONTENT") or "",
                    "receive_date": row.get("RECEIVE_DATE"),
                }
            )
        return rows


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("receive_date must be in YYYY-MM-DD format") from exc


def _required(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"Missing required database env var: {name}")
    return value


def _import_pymssql():
    try:
        import pymssql
    except ImportError as exc:
        raise ImportError("pymssql is required for SQL Server input. Install requirements.txt.") from exc
    return pymssql
