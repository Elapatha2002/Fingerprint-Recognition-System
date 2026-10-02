"""Provision a private Supabase schema for the recognition demonstration."""
from __future__ import annotations

import getpass
import os
import secrets
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from demo_matcher.auth import hash_password


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ROLE = "fingerprint_demo_runtime"


def _require_ssl(url: str) -> str:
    parts = urlsplit(url)
    query = parts.query
    if "sslmode=" not in query:
        query = f"{query}&sslmode=require" if query else "sslmode=require"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _runtime_url(admin_url: str, password: str) -> str:
    parts = urlsplit(admin_url)
    admin_user = unquote(parts.username or "")
    if not parts.hostname or not admin_user:
        raise ValueError("The database connection string is incomplete.")
    suffix = admin_user.split(".", 1)[1] if "." in admin_user else ""
    runtime_user = f"{ROLE}.{suffix}" if suffix else ROLE
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{quote(runtime_user, safe='')}:{quote(password, safe='')}@{host}"
    query = parts.query
    if "sslmode=" not in query:
        query = f"{query}&sslmode=require" if query else "sslmode=require"
    return urlunsplit((parts.scheme, netloc, parts.path or "/postgres", query, ""))


def _role_statement(action: str, password: str) -> sql.Composed:
    """Build CREATE/ALTER ROLE safely without a server-side bind marker.

    PostgreSQL utility statements do not accept ``$1`` for a role password.
    ``sql.Literal`` still quotes the generated password through psycopg, while
    ``sql.Identifier`` safely quotes the fixed role name.
    """
    if action not in {"CREATE", "ALTER"}:
        raise ValueError("Role action must be CREATE or ALTER.")
    return sql.SQL(
        "{} ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOINHERIT"
    ).format(
        sql.SQL(action),
        sql.Identifier(ROLE),
        sql.Literal(password),
    )


def main() -> int:
    print("Fingerprint Recognition System - Supabase setup")
    print("Use the Session pooler connection string from the NEW Supabase project.")
    admin_url = _require_ssl(
        getpass.getpass("Paste the administrator DATABASE_URL: ").strip()
    )
    access_password = getpass.getpass("Choose the application access password: ")
    confirm = getpass.getpass("Repeat the application access password: ")
    if access_password != confirm:
        raise ValueError("The application passwords do not match.")
    access_hash = hash_password(access_password)
    runtime_password = secrets.token_urlsafe(32)
    runtime_url = _runtime_url(admin_url, runtime_password)

    confirmation = input("Type SETUP to create the private schema: ").strip()
    if confirmation != "SETUP":
        print("Cancelled. Nothing was changed.")
        return 1

    with psycopg.connect(admin_url, connect_timeout=10, autocommit=True) as connection:
        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)
        ).fetchone()
        if role_exists:
            connection.execute(_role_statement("ALTER", runtime_password))
        else:
            connection.execute(_role_statement("CREATE", runtime_password))
        connection.execute("CREATE SCHEMA IF NOT EXISTS fingerprint_demo")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS fingerprint_demo.enrolments (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                finger_label TEXT NOT NULL,
                template BYTEA NOT NULL,
                enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (octet_length(template) BETWEEN 1024 AND 131072)
            )"""
        )
        connection.execute("REVOKE ALL ON SCHEMA fingerprint_demo FROM PUBLIC")
        connection.execute("REVOKE ALL ON ALL TABLES IN SCHEMA fingerprint_demo FROM PUBLIC")
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(connection.info.dbname), sql.Identifier(ROLE)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA fingerprint_demo TO {}").format(
                sql.Identifier(ROLE)
            )
        )
        connection.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON "
                    "fingerprint_demo.enrolments TO {}").format(sql.Identifier(ROLE))
        )

    ENV_PATH.write_text(
        "# Private runtime configuration. Never commit this file.\n"
        f"DATABASE_URL={runtime_url}\n"
        f"FRS_ACCESS_PASSWORD_HASH={access_hash}\n"
        "MATCH_THRESHOLD=0.55\n"
        "MANTRA_SENSOR_TRANSPORT=auto\n"
        "MANTRA_BRIDGE_URL=http://127.0.0.1:8766\n",
        encoding="utf-8",
    )
    os.environ["DATABASE_URL"] = runtime_url
    print(f"Setup complete. Private configuration saved to {ENV_PATH.name}.")
    print("The Supabase administrator password was not saved.")
    print("Start locally with: python -m streamlit run demo_matcher/streamlit_app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
