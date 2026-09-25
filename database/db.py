import os
import sqlite3

import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv


load_dotenv()


DATABASE_URL = os.getenv("DATABASE_URL")

SQLITE_DATABASE = "database/database.db"


class PostgresCursor:
    """
    Small wrapper around psycopg2 cursor.

    It keeps the existing Flask application's
    execute(), fetchone(), fetchall() style working.
    """

    def __init__(self, cursor, lastrowid=None):
        self.cursor = cursor
        self._lastrowid = lastrowid

    @property
    def lastrowid(self):
        return self._lastrowid

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self.cursor.fetchmany()

        return self.cursor.fetchmany(size)

    def __iter__(self):
        return iter(self.cursor)

    def close(self):
        self.cursor.close()


class PostgresConnection:
    """
    Compatibility layer that makes PostgreSQL behave
    similarly to the existing SQLite connection used
    by Havenly.
    """

    def __init__(self, database_url):
        self.connection = psycopg2.connect(
            database_url,
            cursor_factory=DictCursor
        )

    def execute(self, query, parameters=None):
        """
        Convert SQLite '?' placeholders into PostgreSQL
        '%s' placeholders.
        """

        if parameters is None:
            parameters = ()

        postgres_query = query.replace("?", "%s")

        cursor = self.connection.cursor()

        lastrowid = None

        # The existing Havenly application uses cursor.lastrowid
        # after inserting a property.
        #
        # PostgreSQL does not provide SQLite-style lastrowid,
        # so we use RETURNING id for property inserts.
        normalized_query = postgres_query.strip().lower()

        if (
            normalized_query.startswith(
                "insert into properties"
            )
            and "returning" not in normalized_query
        ):
            postgres_query = (
                postgres_query.rstrip().rstrip(";")
                + " RETURNING id"
            )

            cursor.execute(
                postgres_query,
                parameters
            )

            returned_row = cursor.fetchone()

            if returned_row is not None:
                lastrowid = returned_row["id"]

        else:
            cursor.execute(
                postgres_query,
                parameters
            )

        return PostgresCursor(
            cursor,
            lastrowid
        )

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_db_connection():
    """
    Use Supabase PostgreSQL when DATABASE_URL exists.

    If DATABASE_URL is not configured, fall back to the
    original SQLite database so the project can still
    work locally.
    """

    if DATABASE_URL:
        return PostgresConnection(
            DATABASE_URL
        )

    connection = sqlite3.connect(
        SQLITE_DATABASE
    )

    connection.row_factory = sqlite3.Row

    return connection