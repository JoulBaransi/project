"""db.py — MySQL connection helpers and a graceful health probe.

The whole app gets its DB connections from here (one config system, shared with
config.py). retrieval.py / ingest.py will be wired to use these helpers in their
respective phases instead of opening their own connections.
"""

import mysql.connector

from app import config


def get_connection(use_database: bool = True):
    """Open a MySQL connection.

    use_database=True  -> connect with config.DB_NAME selected (normal app use).
    use_database=False -> connect to the server only (no database selected), so
                          we can probe/bootstrap before the schema exists.
    """
    kwargs = config.DB_CONFIG if use_database else config.DB_CONFIG_NO_DB
    return mysql.connector.connect(**kwargs)


def _database_exists(cursor, db_name: str) -> bool:
    cursor.execute(
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
        (db_name,),
    )
    return cursor.fetchone() is not None


def _table_exists(cursor, db_name: str, table: str) -> bool:
    cursor.execute(
        """
        SELECT TABLE_NAME FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (db_name, table),
    )
    return cursor.fetchone() is not None


def health() -> dict:
    """Report DB status without crashing if the schema isn't there yet.

    Returns one of:
        {"db": "unreachable", "error": "..."}                  # can't connect
        {"db": "connected, database missing"}                  # server up, no DB
        {"db": "connected, not initialized"}                   # DB up, no tables
        {"db": "ok"}                                           # docs_lines present

    This lets phase (a) report a healthy API even though the tables are created
    in phase (b).
    """
    try:
        conn = get_connection(use_database=False)
    except mysql.connector.Error as e:
        return {"db": "unreachable", "error": str(e)}

    try:
        cur = conn.cursor()
        if not _database_exists(cur, config.DB_NAME):
            return {"db": "connected, database missing", "database": config.DB_NAME}
        if not _table_exists(cur, config.DB_NAME, "docs_lines"):
            return {"db": "connected, not initialized", "database": config.DB_NAME}
        return {"db": "ok", "database": config.DB_NAME}
    finally:
        conn.close()
