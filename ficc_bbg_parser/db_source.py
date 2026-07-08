from datetime import datetime, timedelta
from typing import List, Optional

from . import settings


def fetch_chat_email_content(receive_date: str) -> str:
    rows = fetch_chat_email_rows(receive_date)
    return "\n\n".join(row["content"] for row in rows if row["content"])


def fetch_chat_email_rows(receive_date: str) -> List[dict]:
    pyodbc = _import_pyodbc()
    start_dt = _parse_date(receive_date)
    end_dt = start_dt + timedelta(days=1)
    sql = f"""
SELECT SUBJECT, CONTENT, RECEIVE_DATE
FROM {settings.DB_TABLE}
WHERE RECEIVE_DATE >= ? AND RECEIVE_DATE < ?
ORDER BY RECEIVE_DATE
"""

    with pyodbc.connect(_connection_string()) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, start_dt, end_dt)
        rows = []
        for subject, content, row_receive_date in cursor.fetchall():
            rows.append(
                {
                    "subject": subject,
                    "content": content or "",
                    "receive_date": row_receive_date,
                }
            )
        return rows


def _connection_string() -> str:
    required = {
        "DB_USERNAME": settings.DB_USERNAME,
        "DB_PASSWORD": settings.DB_PASSWORD,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required database env vars: {', '.join(missing)}")

    return (
        f"DRIVER={{{settings.DB_DRIVER}}};"
        f"SERVER={settings.DB_SERVER},{settings.DB_PORT};"
        f"DATABASE={settings.DB_DATABASE};"
        f"UID={settings.DB_USERNAME};"
        f"PWD={settings.DB_PASSWORD};"
        f"Encrypt={settings.DB_ENCRYPT};"
        f"TrustServerCertificate={settings.DB_TRUST_SERVER_CERTIFICATE};"
    )


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("receive_date must be in YYYY-MM-DD format") from exc


def _import_pyodbc():
    try:
        import pyodbc
    except ImportError as exc:
        raise ImportError("pyodbc is required for SQL Server input. Install requirements.txt.") from exc
    return pyodbc
