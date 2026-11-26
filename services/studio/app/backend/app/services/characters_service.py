"""Character profiles: a name with base tags, LoRAs and an optional reference image, reusable in any request."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.services.db import get_connection

STAND_IN_WORD = "소녀"  # replaces the character's name in the request text so the LLM does not treat it as a tag


def ensure_table() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            aliases TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            loras TEXT NOT NULL DEFAULT '[]',
            reference_image TEXT,
            use_reference INTEGER NOT NULL DEFAULT 0,
            ip_weight REAL NOT NULL DEFAULT 0.2,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_name_nocase ON characters(name COLLATE NOCASE)")
    conn.commit()
    conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["loras"] = json.loads(d.get("loras") or "[]")
    except ValueError:
        d["loras"] = []
    d["use_reference"] = bool(d["use_reference"])
    d["alias_list"] = [a.strip() for a in d["aliases"].split(",") if a.strip()]
    return d


def list_characters() -> list[dict]:
    ensure_table()
    conn = get_connection()
    rows = conn.execute("SELECT * FROM characters ORDER BY name COLLATE NOCASE").fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_character(character_id: int) -> Optional[dict]:
    ensure_table()
    conn = get_connection()
    row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def _clean_loras(loras: list) -> list[dict]:
    out = []
    for l in loras or []:
        name = str(l.get("name", "")).strip()
        if name:
            out.append({"name": name, "weight": float(l.get("weight", 0.8))})
    return out


def create_character(
    name: str, aliases: str = "", tags: str = "", loras: list | None = None, reference_image: str | None = None,
    use_reference: bool = False, ip_weight: float = 0.2, notes: str = "",
) -> int:
    ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO characters (name, aliases, tags, loras, reference_image, use_reference, ip_weight, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name.strip(), aliases.strip(), tags.strip(), json.dumps(_clean_loras(loras or [])), reference_image,
             int(use_reference), ip_weight, notes.strip(), now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_character(character_id: int, **fields) -> bool:
    ensure_table()
    allowed = {"name", "aliases", "tags", "loras", "reference_image", "use_reference", "ip_weight", "notes"}
    sets, values = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "loras":
            v = json.dumps(_clean_loras(v))
        elif k == "use_reference":
            v = int(v)
        elif isinstance(v, str):
            v = v.strip()
        sets.append(f"{k} = ?")
        values.append(v)
    if not sets:
        return False
    sets.append("updated_at = ?")
    values.append(datetime.now(timezone.utc).isoformat())
    values.append(character_id)
    conn = get_connection()
    try:
        cur = conn.execute(f"UPDATE characters SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_character(character_id: int) -> bool:
    ensure_table()
    conn = get_connection()
    cur = conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ------------------------------------------------------------------ using profiles in a request
def _name_pattern(name: str) -> re.Pattern:
    escaped = re.escape(name)
    if re.search(r"[A-Za-z0-9]", name):  # latin names must not match inside longer words
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped)  # Korean names are followed by particles (히비가), so no boundary


def detect_characters(message: str, explicit_ids: list[int] | None = None) -> list[dict]:
    """Profiles chosen explicitly plus any whose name/alias appears in the message (longest names first)."""
    chosen: dict[int, dict] = {}
    for cid in explicit_ids or []:
        c = get_character(cid)
        if c:
            chosen[c["id"]] = c
    candidates = list_characters()
    candidates.sort(key=lambda c: -max(len(n) for n in [c["name"], *c["alias_list"]]))
    for c in candidates:
        if c["id"] in chosen:
            continue
        for n in [c["name"], *c["alias_list"]]:
            if n and _name_pattern(n).search(message):
                chosen[c["id"]] = c
                break
    return list(chosen.values())


# 소녀 ends in a vowel, so a particle that followed a consonant-final name changes form
_PARTICLES = {"이랑": "랑", "이": "가", "가": "가", "은": "는", "는": "는", "을": "를", "를": "를", "과": "와", "와": "와", "랑": "랑", "의": "의", "도": "도"}
_PARTICLE_RE = "(이랑|이|가|은|는|을|를|과|와|랑|의|도)?"


def mask_names(message: str, characters: list[dict]) -> str:
    """Replace every mention of the characters' names with a neutral word for the LLM."""
    out = message
    for c in characters:
        for n in sorted([c["name"], *c["alias_list"]], key=len, reverse=True):
            if not n:
                continue
            base = _name_pattern(n)
            if re.search(r"[A-Za-z0-9]", n):
                out = base.sub(STAND_IN_WORD, out)
            else:
                # keep a directly following particle, in the form that fits the new vowel-final word
                out = re.sub(base.pattern + _PARTICLE_RE, lambda m: STAND_IN_WORD + _PARTICLES.get(m.group(1) or "", ""), out)
    return out


def _split(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def merge_prompt(characters: list[dict], llm_prompt: str) -> str:
    """Character tags first (they carry the identity), then the request's own tags without duplicates."""
    tags: list[str] = []
    seen: set[str] = set()
    for c in characters:
        for t in _split(c["tags"]):
            if t.lower() not in seen:
                tags.append(t)
                seen.add(t.lower())
    names = {n.lower() for c in characters for n in [c["name"], *c["alias_list"]]}
    for t in _split(llm_prompt):
        low = t.lower()
        if low in seen or (low in names and low not in seen):
            continue
        tags.append(t)
        seen.add(low)
    return ", ".join(tags)


def merge_loras(characters: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for c in characters:
        for l in c["loras"]:
            merged.setdefault(l["name"], l)
    return list(merged.values())
