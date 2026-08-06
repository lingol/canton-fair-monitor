import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterable, List, Optional, cast


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def query_one(self, sql: str, parameters: Iterable[object] = ()) -> Optional[sqlite3.Row]:
        with self.connect() as connection:
            return cast(
                Optional[sqlite3.Row], connection.execute(sql, tuple(parameters)).fetchone()
            )

    def query_all(self, sql: str, parameters: Iterable[object] = ()) -> List[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(sql, tuple(parameters)).fetchall()
