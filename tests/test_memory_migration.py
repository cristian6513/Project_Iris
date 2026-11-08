"""Migration coverage for profiles created before the IRIS rename."""

from __future__ import annotations

import asyncio
import sqlite3

from core.config import IRISConfig
from core.memory import IRISMemory


def test_legacy_alice_conversation_constraint_is_migrated(tmp_path) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as db:
        db.execute("""
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'alice', 'system')),
                content TEXT NOT NULL,
                emotion TEXT DEFAULT 'neutral',
                session_id TEXT DEFAULT 'default',
                metadata TEXT DEFAULT '{}'
            )
        """)
        db.execute(
            "INSERT INTO conversations(timestamp, role, content) VALUES(1, 'alice', 'legado')"
        )

    async def exercise() -> None:
        memory = IRISMemory(IRISConfig(memory_db_path=str(path)))
        await memory.initialize()
        await memory.remember("IRIS", "respuesta nueva")
        rows = await memory.recall()
        await memory.close()
        assert [row["role"] for row in rows] == ["IRIS", "IRIS"]

    asyncio.run(exercise())

    with sqlite3.connect(path) as db:
        schema = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversations'"
        ).fetchone()[0]
    assert "'IRIS'" in schema
    assert "'alice'" not in schema
