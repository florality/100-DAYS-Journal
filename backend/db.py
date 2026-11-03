from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .tasks_data import TASKS

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "yibai.db"
MEDIA_DIR = PROJECT_DIR / "media"
PUBLIC_DIR = PROJECT_DIR / "public"
DATABASE_URL = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()
IS_VERCEL = bool(os.getenv("VERCEL"))


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def using_postgres() -> bool:
    return bool(DATABASE_URL)


class PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: tuple | list = ()) -> Any:
        return self._connection.execute(sql.replace("?", "%s"), params)

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


def connect() -> Any:
    if using_postgres():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("线上模式需要安装 psycopg[binary]") from exc
        connection = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=10,
            prepare_threshold=None,
        )
        return PostgresConnection(connection)

    if IS_VERCEL:
        raise RuntimeError(
            "Vercel 环境缺少 DATABASE_URL。请连接 Neon/Postgres，并把 DATABASE_URL 注入项目环境变量。"
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '你',
    target_count INTEGER NOT NULL DEFAULT 100,
    duration_days INTEGER NOT NULL DEFAULT 365,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    minutes_min INTEGER NOT NULL DEFAULT 1,
    minutes_standard INTEGER NOT NULL DEFAULT 10,
    minutes_adventure INTEGER NOT NULL DEFAULT 30,
    social TEXT NOT NULL DEFAULT 'solo',
    place TEXT NOT NULL DEFAULT 'anywhere',
    cost INTEGER NOT NULL DEFAULT 0,
    difficulty INTEGER NOT NULL DEFAULT 1,
    color TEXT NOT NULL DEFAULT '#C9B8A2',
    min_action TEXT NOT NULL,
    standard_action TEXT NOT NULL,
    adventure_action TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '现在，你想记住什么？',
    is_custom INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    version TEXT NOT NULL DEFAULT 'standard',
    intent TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    selected_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_task_id INTEGER NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    photo_path TEXT,
    mood_after INTEGER,
    color TEXT NOT NULL DEFAULT '#D8A47F',
    repeat_wish INTEGER NOT NULL DEFAULT 0,
    is_private INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_task_id) REFERENCES user_tasks(id)
);

CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent TEXT NOT NULL,
    energy INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    data BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_tasks_status ON user_tasks(status);
CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at);
"""

POSTGRES_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        name TEXT NOT NULL DEFAULT '你',
        target_count INTEGER NOT NULL DEFAULT 100,
        duration_days INTEGER NOT NULL DEFAULT 365,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id BIGSERIAL PRIMARY KEY,
        slug TEXT UNIQUE,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        category TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        minutes_min INTEGER NOT NULL DEFAULT 1,
        minutes_standard INTEGER NOT NULL DEFAULT 10,
        minutes_adventure INTEGER NOT NULL DEFAULT 30,
        social TEXT NOT NULL DEFAULT 'solo',
        place TEXT NOT NULL DEFAULT 'anywhere',
        cost INTEGER NOT NULL DEFAULT 0,
        difficulty INTEGER NOT NULL DEFAULT 1,
        color TEXT NOT NULL DEFAULT '#C9B8A2',
        min_action TEXT NOT NULL,
        standard_action TEXT NOT NULL,
        adventure_action TEXT NOT NULL,
        prompt TEXT NOT NULL DEFAULT '现在，你想记住什么？',
        is_custom INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_tasks (
        id BIGSERIAL PRIMARY KEY,
        task_id BIGINT NOT NULL REFERENCES tasks(id),
        version TEXT NOT NULL DEFAULT 'standard',
        intent TEXT,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        selected_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entries (
        id BIGSERIAL PRIMARY KEY,
        user_task_id BIGINT NOT NULL UNIQUE REFERENCES user_tasks(id),
        note TEXT NOT NULL DEFAULT '',
        photo_path TEXT,
        mood_after INTEGER,
        color TEXT NOT NULL DEFAULT '#D8A47F',
        repeat_wish INTEGER NOT NULL DEFAULT 0,
        is_private INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkins (
        id BIGSERIAL PRIMARY KEY,
        intent TEXT NOT NULL,
        energy INTEGER,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS photos (
        id TEXT PRIMARY KEY,
        content_type TEXT NOT NULL,
        data BYTEA NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_tasks_status ON user_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at)",
]


def init_db() -> None:
    connection = connect()
    try:
        if using_postgres():
            for statement in POSTGRES_SCHEMA:
                connection.execute(statement)
        else:
            connection.executescript(SQLITE_SCHEMA)

        connection.execute(
            """
            INSERT INTO profile (id, name, target_count, duration_days, created_at)
            VALUES (1, '你', 100, 365, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            (now_iso(),),
        )

        count_row = connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
        if int(count_row["n"]) < len(TASKS):
            _seed_tasks(connection)
        connection.commit()
    finally:
        connection.close()


def _seed_tasks(connection: Any) -> None:
    for task in TASKS:
        connection.execute(
            """
            INSERT INTO tasks (
                slug, title, summary, category, tags,
                minutes_min, minutes_standard, minutes_adventure,
                social, place, cost, difficulty, color,
                min_action, standard_action, adventure_action, prompt, is_custom, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT (slug) DO NOTHING
            """,
            (
                task["slug"],
                task["title"],
                task["summary"],
                task["category"],
                json.dumps(task["tags"], ensure_ascii=False),
                task["minutes"][0],
                task["minutes"][1],
                task["minutes"][2],
                task["social"],
                task["place"],
                task["cost"],
                task["difficulty"],
                task["color"],
                task["actions"][0],
                task["actions"][1],
                task["actions"][2],
                task["prompt"],
                now_iso(),
            ),
        )


def serialize_task(row: Any) -> dict:
    slug = row["slug"] or ""
    if slug.startswith("ai-"):
        source = "ai"
    elif row["is_custom"]:
        source = "custom"
    else:
        source = "curated"
    return {
        "id": row["id"],
        "slug": row["slug"],
        "source": source,
        "title": row["title"],
        "summary": row["summary"],
        "category": row["category"],
        "tags": json.loads(row["tags"] or "[]"),
        "minutes": {
            "min": row["minutes_min"],
            "standard": row["minutes_standard"],
            "adventure": row["minutes_adventure"],
        },
        "social": row["social"],
        "place": row["place"],
        "cost": row["cost"],
        "difficulty": row["difficulty"],
        "color": row["color"],
        "versions": {
            "min": row["min_action"],
            "standard": row["standard_action"],
            "adventure": row["adventure_action"],
        },
        "prompt": row["prompt"],
        "is_custom": bool(row["is_custom"]),
    }


def task_row_to_dict(row: Any) -> dict:
    return serialize_task(row)
