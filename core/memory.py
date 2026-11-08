"""
IRIS Memory — Persistent SQLite Memory System

Stores conversations, preferences, context history, and system state.
IRIS remembers everything across sessions.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from core.config import IRISConfig


class IRISMemory:
    """Persistent memory system using SQLite."""

    def __init__(self, config: IRISConfig) -> None:
        self.db_path = config.get_memory_db_path()
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection and create tables if needed."""
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()

    async def _create_tables(self) -> None:
        """Create all memory tables."""
        assert self._db is not None

        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'IRIS', 'system')),
                content TEXT NOT NULL,
                emotion TEXT DEFAULT 'neutral',
                session_id TEXT DEFAULT 'default',
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS context_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                summary TEXT NOT NULL,
                session_id TEXT DEFAULT 'default',
                token_count INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_session
                ON conversations(session_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_conversations_timestamp
                ON conversations(timestamp);
        """)
        await self._migrate_legacy_conversation_roles()
        await self._db.commit()

    async def _migrate_legacy_conversation_roles(self) -> None:
        """Replace the pre-IRIS ``alice`` role constraint without data loss.

        SQLite does not update a CHECK constraint when ``CREATE TABLE IF NOT
        EXISTS`` runs. Profiles created under the former project name therefore
        kept accepting ``alice`` but rejected every new ``IRIS`` assistant
        turn. Rebuild the small conversation table transactionally and map all
        assistant-like legacy values to the current canonical role.
        """
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversations'"
        )
        row = await cursor.fetchone()
        schema = str(row[0] or "") if row else ""
        normalized = schema.casefold()
        if "'iris'" in normalized and "'alice'" not in normalized:
            return

        await self._db.execute("BEGIN IMMEDIATE")
        try:
            await self._db.execute("DROP TABLE IF EXISTS conversations_iris_migration")
            await self._db.execute("""
                CREATE TABLE conversations_iris_migration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'IRIS', 'system')),
                    content TEXT NOT NULL,
                    emotion TEXT DEFAULT 'neutral',
                    session_id TEXT DEFAULT 'default',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await self._db.execute("""
                INSERT INTO conversations_iris_migration
                    (id, timestamp, role, content, emotion, session_id, metadata)
                SELECT
                    id,
                    timestamp,
                    CASE
                        WHEN lower(role) = 'user' THEN 'user'
                        WHEN lower(role) = 'system' THEN 'system'
                        ELSE 'IRIS'
                    END,
                    content,
                    emotion,
                    session_id,
                    metadata
                FROM conversations
            """)
            await self._db.execute("DROP TABLE conversations")
            await self._db.execute(
                "ALTER TABLE conversations_iris_migration RENAME TO conversations"
            )
            await self._db.execute("""
                CREATE INDEX idx_conversations_session
                    ON conversations(session_id, timestamp)
            """)
            await self._db.execute("""
                CREATE INDEX idx_conversations_timestamp
                    ON conversations(timestamp)
            """)
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    async def remember(
        self,
        role: str,
        content: str,
        emotion: str = "neutral",
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a conversation entry. Returns the entry ID."""
        assert self._db is not None

        cursor = await self._db.execute(
            """INSERT INTO conversations (timestamp, role, content, emotion, session_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                role,
                content,
                emotion,
                session_id,
                json.dumps(metadata or {}),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid or 0

    async def recall(
        self,
        session_id: str = "default",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve recent conversation entries."""
        assert self._db is not None

        cursor = await self._db.execute(
            """SELECT id, timestamp, role, content, emotion, metadata
               FROM conversations
               WHERE session_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "timestamp": row[1],
                "role": row[2],
                "content": row[3],
                "emotion": row[4],
                "metadata": json.loads(row[5]),
            }
            for row in reversed(rows)  # Return in chronological order
        ]

    async def save_preference(self, key: str, value: str) -> None:
        """Save a user preference."""
        assert self._db is not None

        await self._db.execute(
            """INSERT INTO preferences (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, time.time()),
        )
        await self._db.commit()

    async def get_preference(self, key: str, default: str = "") -> str:
        """Get a user preference."""
        assert self._db is not None

        cursor = await self._db.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else default


    # ── Memory Panel queries (face UI's 🧠 modal) ──────────────────
    #
    # These power the "remind IRIS" modal in the face layer. Kept
    # separate from `recall()` because the modal needs date-range +
    # keyword filters and pagination, which the conversational
    # context-window path doesn't.

    async def query_turns(
        self,
        *,
        session_id: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        keyword: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Filtered, paginated list of conversation turns.

        Filters are AND-combined. `keyword` does a case-insensitive
        LIKE on `content`. `session_id=None` means all sessions.
        Returned in CHRONOLOGICAL order (oldest first) so the modal
        can pair user+IRIS turns naturally.
        """
        assert self._db is not None
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if from_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("timestamp <= ?")
            params.append(to_ts)
        if keyword:
            clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{keyword.lower()}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([int(limit), int(offset)])
        cursor = await self._db.execute(
            f"""SELECT id, timestamp, role, content, emotion, session_id, metadata
               FROM conversations
               {where}
               ORDER BY timestamp ASC
               LIMIT ? OFFSET ?""",
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id":         r[0],
                "timestamp":  r[1],
                "role":       r[2],
                "content":    r[3],
                "emotion":    r[4],
                "session_id": r[5],
                "metadata":   json.loads(r[6] or "{}"),
            }
            for r in rows
        ]

    async def count_turns(
        self,
        *,
        session_id: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        keyword: str | None = None,
    ) -> int:
        """Same filter semantics as `query_turns`, returns total count."""
        assert self._db is not None
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if from_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts is not None:
            clauses.append("timestamp <= ?")
            params.append(to_ts)
        if keyword:
            clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{keyword.lower()}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM conversations {where}", params,
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def get_turns_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        """Fetch a specific set of turns by primary key, chronological."""
        assert self._db is not None
        if not ids:
            return []
        # Validate to integers up front — caller is the HTTP layer and
        # we don't want any string sneaking into a SQL `IN (…)` build.
        clean = [int(i) for i in ids]
        placeholders = ",".join("?" * len(clean))
        cursor = await self._db.execute(
            f"""SELECT id, timestamp, role, content, emotion, session_id, metadata
               FROM conversations
               WHERE id IN ({placeholders})
               ORDER BY timestamp ASC""",
            clean,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id":         r[0],
                "timestamp":  r[1],
                "role":       r[2],
                "content":    r[3],
                "emotion":    r[4],
                "session_id": r[5],
                "metadata":   json.loads(r[6] or "{}"),
            }
            for r in rows
        ]

    async def delete_turns_by_ids(self, ids: list[int]) -> int:
        """Delete specific turns. Returns rows affected."""
        assert self._db is not None
        if not ids:
            return 0
        clean = [int(i) for i in ids]
        placeholders = ",".join("?" * len(clean))
        cursor = await self._db.execute(
            f"DELETE FROM conversations WHERE id IN ({placeholders})",
            clean,
        )
        await self._db.commit()
        return cursor.rowcount or 0

    async def clear_history(self, session_id: str | None = None) -> int:
        """Wipe local conversation history.

        `session_id=None` clears EVERY session — used by the modal's
        "limpiar todo" button. Returns rows affected so the UI can
        confirm with a real number.
        """
        assert self._db is not None
        if session_id is None:
            cursor = await self._db.execute("DELETE FROM conversations")
        else:
            cursor = await self._db.execute(
                "DELETE FROM conversations WHERE session_id = ?",
                (session_id,),
            )
        await self._db.commit()
        return cursor.rowcount or 0

    async def memory_stats(self, session_id: str | None = None) -> dict[str, Any]:
        """Quick stats for the modal header: total count, time range."""
        assert self._db is not None
        if session_id is None:
            cursor = await self._db.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM conversations"
            )
        else:
            cursor = await self._db.execute(
                """SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                   FROM conversations WHERE session_id = ?""",
                (session_id,),
            )
        row = await cursor.fetchone()
        return {
            "count":     int(row[0]) if row and row[0] is not None else 0,
            "oldest_ts": float(row[1]) if row and row[1] is not None else None,
            "newest_ts": float(row[2]) if row and row[2] is not None else None,
        }

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None


# Legacy extension alias.
AliceMemory = IRISMemory

