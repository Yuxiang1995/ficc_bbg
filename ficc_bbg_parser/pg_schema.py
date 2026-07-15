from pathlib import Path
from typing import List

from . import settings


SQL_DIR = Path(__file__).with_name("sql")


def initialize_schema() -> List[str]:
    psycopg = _import_psycopg()
    migration_files = sorted(SQL_DIR.glob("*.sql"))
    if not migration_files:
        raise RuntimeError(f"No PostgreSQL migrations found in {SQL_DIR}")

    applied = []
    with psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DATABASE,
        user=settings.POSTGRES_USERNAME,
        password=_required("POSTGRES_PASSWORD", settings.POSTGRES_PASSWORD),
        sslmode=settings.POSTGRES_SSLMODE,
    ) as connection:
        with connection.cursor() as cursor:
            for migration_file in migration_files:
                version = migration_file.stem
                if _is_migration_applied(cursor, psycopg, version):
                    continue
                cursor.execute(migration_file.read_text(encoding="utf-8"), prepare=False)
                applied.append(migration_file.name)

    return applied


def main() -> None:
    applied = initialize_schema()
    print(f"database={settings.POSTGRES_DATABASE}")
    print(f"schema={settings.POSTGRES_SCHEMA}")
    print(f"migrations={','.join(applied) if applied else 'none'}")


def _is_migration_applied(cursor, psycopg, version: str) -> bool:
    relation_name = f"{settings.POSTGRES_SCHEMA}.schema_migration"
    cursor.execute("SELECT to_regclass(%s)", (relation_name,))
    if cursor.fetchone()[0] is None:
        return False

    query = psycopg.sql.SQL("SELECT 1 FROM {}.schema_migration WHERE version = %s").format(
        psycopg.sql.Identifier(settings.POSTGRES_SCHEMA)
    )
    cursor.execute(query, (version,))
    return cursor.fetchone() is not None


def _required(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"Missing required PostgreSQL env var: {name}")
    return value


def _import_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise ImportError("psycopg is required for PostgreSQL. Install requirements.txt.") from exc
    return psycopg


if __name__ == "__main__":
    main()
