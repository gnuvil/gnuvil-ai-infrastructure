import sqlite3
import json
from datetime import datetime, timezone

from app.core.config import DATABASE_PATH
from app.services.request_logging import associate_generation_with_current_request

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT NOT NULL,
            negative_prompt TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL,
            loras TEXT NOT NULL DEFAULT '[]',
            seed INTEGER NOT NULL,
            steps INTEGER NOT NULL,
            cfg REAL NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            sampler TEXT NOT NULL,
            parent_image_id INTEGER,
            image_path TEXT,
            prompt_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL
        )
        """
    )
    for column, ddl in (("media_type", "TEXT NOT NULL DEFAULT 'image'"), ("frames", "INTEGER NOT NULL DEFAULT 0")):
        try:
            conn.execute(f"ALTER TABLE generations ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass  # column already exists
    # Every file a generation left behind (ComfyUI's own copy and the app copy), so deleting a
    # generation can remove intermediate files too (video segments, inpaint harmonize passes).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generation_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generation_id INTEGER NOT NULL,
            comfy_path TEXT NOT NULL DEFAULT '',
            app_name TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generation_files_gid ON generation_files(generation_id)")
    conn.commit()
    conn.close()


def add_generation_file(generation_id: int, comfy_path: str, app_name: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO generation_files (generation_id, comfy_path, app_name) VALUES (?, ?, ?)",
        (generation_id, comfy_path, app_name),
    )
    conn.commit()
    conn.close()


def list_generation_files(generation_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM generation_files WHERE generation_id = ?", (generation_id,)).fetchall()
    conn.close()
    return rows


def list_generations_page(offset: int, limit: int, media: str | None) -> tuple[list[sqlite3.Row], int]:
    """Finished generations that have a result file, newest first, with the total count for paging."""
    where = "status = 'completed' AND image_path IS NOT NULL"
    args: list = []
    if media in ("image", "video"):
        where += " AND media_type = ?"
        args.append(media)
    conn = get_connection()
    total = conn.execute(f"SELECT COUNT(*) FROM generations WHERE {where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM generations WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset]
    ).fetchall()
    conn.close()
    return rows, total


def delete_generation(generation_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM generation_files WHERE generation_id = ?", (generation_id,))
    conn.execute("DELETE FROM generations WHERE id = ?", (generation_id,))
    conn.commit()
    conn.close()


def create_generation(**kwargs) -> int:
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO generations
            (prompt, negative_prompt, model, loras, seed, steps, cfg, width, height,
             sampler, parent_image_id, prompt_id, status, created_at, media_type, frames)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kwargs["prompt"],
            kwargs.get("negative_prompt", ""),
            kwargs["model"],
            json.dumps(kwargs.get("loras", [])),
            kwargs["seed"],
            kwargs["steps"],
            kwargs["cfg"],
            kwargs["width"],
            kwargs["height"],
            kwargs["sampler"],
            kwargs.get("parent_image_id"),
            kwargs.get("prompt_id"),
            kwargs.get("status", "queued"),
            datetime.now(timezone.utc).isoformat(),
            kwargs.get("media_type", "image"),
            kwargs.get("frames", 0),
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    associate_generation_with_current_request(new_id)
    return new_id


def update_generation_status(generation_id: int, status: str, image_path: str | None = None):
    conn = get_connection()
    if image_path is not None:
        conn.execute(
            "UPDATE generations SET status = ?, image_path = ? WHERE id = ?",
            (status, image_path, generation_id),
        )
    else:
        conn.execute(
            "UPDATE generations SET status = ? WHERE id = ?",
            (status, generation_id),
        )
    conn.commit()
    conn.close()


def update_generation_dimensions(generation_id: int, width: int, height: int):
    conn = get_connection()
    conn.execute(
        "UPDATE generations SET width = ?, height = ? WHERE id = ?",
        (width, height, generation_id),
    )
    conn.commit()
    conn.close()


def get_generation(generation_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM generations WHERE id = ?", (generation_id,)).fetchone()
    conn.close()
    return row


def get_generations(generation_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not generation_ids:
        return {}
    placeholders = ",".join("?" for _ in generation_ids)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT id, status, image_path, media_type FROM generations WHERE id IN ({placeholders})",
        generation_ids,
    ).fetchall()
    conn.close()
    return {row["id"]: row for row in rows}


def list_generations(limit: int = 50) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM generations ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows
