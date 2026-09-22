"""Persistent, bounded chat-message cache and classification watermarks."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


RESULT_VERSION = 2
MBTI_TYPES = {
    "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP",
    "ESTP", "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ",
}
MAX_PERSON_MESSAGES = 1_000
MAX_CHAT_MESSAGES = 20_000
MAX_TEXT_CHARS = 8_000


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
            except ValueError:
                return None
            # Naive CLI values such as YYYY-MM-DD HH:MM are local wall time.
            return parsed.timestamp()
    else:
        return None
    if not math.isfinite(number):
        return None
    # Accept common Unix millisecond, microsecond and nanosecond representations.
    while abs(number) > 253_402_300_799:
        number /= 1_000.0
    return number


def _timestamp_json(value: Any) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        value = ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _message_id(message: dict[str, Any], text: str) -> str:
    value = message.get("id")
    if value is not None and str(value).strip():
        return str(value).strip()
    source = _timestamp_json(message.get("timestamp")) + "\0" + text
    return "synthetic:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _valid_profile(profile: Any) -> bool:
    if not isinstance(profile, dict) or profile.get("result_version") != RESULT_VERSION:
        return False
    probability = profile.get("probability")
    return (
        profile.get("label") in MBTI_TYPES
        and profile.get("status") in {"estimated", "uncertain"}
        and not isinstance(probability, bool)
        and isinstance(probability, (int, float))
        and math.isfinite(probability)
        and 0.0 <= probability <= 1.0
    )


def _profile_baseline(profile: Any) -> tuple[int, float] | None:
    if not _valid_profile(profile):
        return None
    value = profile.get("cache_baseline")
    if not isinstance(value, dict):
        return None
    through_seq = value.get("through_seq")
    latest = value.get("latest_timestamp")
    if (
        isinstance(through_seq, bool)
        or not isinstance(through_seq, int)
        or through_seq < 0
        or isinstance(latest, bool)
        or not isinstance(latest, (int, float))
        or not math.isfinite(latest)
    ):
        return None
    return through_seq, float(latest)


class MessageCache:
    """SQLite cache isolated by chat and sender, safe for distinct caller threads."""

    def __init__(self, path: str | Path | None = None):
        default = Path(__file__).resolve().parents[1] / ".data" / "messages.sqlite3"
        self.path = Path(path).expanduser().resolve() if path is not None else default
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _open(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._open() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS people (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp_json TEXT NOT NULL,
                    timestamp_epoch REAL,
                    UNIQUE (chat_id, user_id, message_id),
                    FOREIGN KEY (chat_id, user_id) REFERENCES people(chat_id, user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS messages_person_order
                    ON messages(chat_id, user_id, timestamp_epoch, seq);
                CREATE INDEX IF NOT EXISTS messages_chat_order
                    ON messages(chat_id, timestamp_epoch, seq);
                CREATE TABLE IF NOT EXISTS observations (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    timestamp_epoch REAL,
                    pending INTEGER NOT NULL CHECK (pending IN (0, 1)),
                    PRIMARY KEY (chat_id, user_id, message_id),
                    FOREIGN KEY (chat_id, user_id) REFERENCES people(chat_id, user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS observations_pending
                    ON observations(chat_id, user_id, pending, seq);
                CREATE TABLE IF NOT EXISTS classification_state (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    baseline_seq INTEGER NOT NULL,
                    latest_timestamp REAL NOT NULL,
                    inclusive INTEGER NOT NULL CHECK (inclusive IN (0, 1)),
                    PRIMARY KEY (chat_id, user_id),
                    FOREIGN KEY (chat_id, user_id) REFERENCES people(chat_id, user_id) ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _identity(chat_id: Any, user_id: Any | None = None) -> tuple[str, str | None]:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id must be non-empty text")
        chat = chat_id.strip()
        if user_id is None:
            return chat, None
        uid = str(user_id).strip()
        if not uid:
            raise ValueError("user_id must be non-empty")
        return chat, uid

    @staticmethod
    def _pending_for(timestamp: float | None, state: sqlite3.Row | None) -> int:
        if state is None or timestamp is None:
            return 1
        cutoff = float(state["latest_timestamp"])
        return int(timestamp >= cutoff if state["inclusive"] else timestamp > cutoff)

    def merge_people(self, chat_id: str, people_mapping: dict) -> None:
        chat, _ = self._identity(chat_id)
        if not isinstance(people_mapping, dict):
            raise TypeError("people_mapping must be a dictionary")
        touched: set[str] = set()
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for key, raw_person in people_mapping.items():
                if not isinstance(raw_person, dict):
                    continue
                raw_uid = raw_person.get("id", key)
                _, uid = self._identity(chat, raw_uid)
                assert uid is not None
                name = raw_person.get("name")
                name = name.strip() if isinstance(name, str) and name.strip() else uid
                connection.execute(
                    """INSERT INTO people(chat_id, user_id, name) VALUES (?, ?, ?)
                       ON CONFLICT(chat_id, user_id) DO UPDATE SET name=excluded.name""",
                    (chat, uid, name),
                )
                state = connection.execute(
                    "SELECT baseline_seq, latest_timestamp, inclusive FROM classification_state WHERE chat_id=? AND user_id=?",
                    (chat, uid),
                ).fetchone()
                raw_messages = raw_person.get("messages", [])
                if not isinstance(raw_messages, list):
                    raw_messages = []
                for raw_message in raw_messages:
                    if not isinstance(raw_message, dict) or not isinstance(raw_message.get("text"), str):
                        continue
                    text = raw_message["text"][:MAX_TEXT_CHARS]
                    if not text.strip():
                        continue
                    message_id = _message_id(raw_message, text)
                    timestamp_value = raw_message.get("timestamp", "")
                    epoch = _timestamp(timestamp_value)
                    existing = connection.execute(
                        "SELECT seq FROM messages WHERE chat_id=? AND user_id=? AND message_id=?",
                        (chat, uid, message_id),
                    ).fetchone()
                    if existing is not None:
                        connection.execute(
                            """UPDATE messages SET text=?, timestamp_json=?, timestamp_epoch=?
                               WHERE chat_id=? AND user_id=? AND message_id=?""",
                            (text, _timestamp_json(timestamp_value), epoch, chat, uid, message_id),
                        )
                        continue
                    cursor = connection.execute(
                        """INSERT INTO messages(chat_id, user_id, message_id, text, timestamp_json, timestamp_epoch)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (chat, uid, message_id, text, _timestamp_json(timestamp_value), epoch),
                    )
                    connection.execute(
                        """INSERT OR IGNORE INTO observations
                           (chat_id, user_id, message_id, seq, timestamp_epoch, pending)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (chat, uid, message_id, cursor.lastrowid, epoch, self._pending_for(epoch, state)),
                    )
                touched.add(uid)

            for uid in touched:
                connection.execute(
                    """DELETE FROM messages WHERE chat_id=? AND user_id=? AND seq NOT IN (
                           SELECT seq FROM messages WHERE chat_id=? AND user_id=?
                           ORDER BY (timestamp_epoch IS NOT NULL) DESC, timestamp_epoch DESC, seq DESC LIMIT ?
                       )""",
                    (chat, uid, chat, uid, MAX_PERSON_MESSAGES),
                )
            connection.execute(
                """DELETE FROM messages WHERE chat_id=? AND seq NOT IN (
                       SELECT seq FROM messages WHERE chat_id=?
                       ORDER BY (timestamp_epoch IS NOT NULL) DESC, timestamp_epoch DESC, seq DESC LIMIT ?
                   )""",
                (chat, chat, MAX_CHAT_MESSAGES),
            )

    @staticmethod
    def _decode_timestamp(value: str) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return ""

    def _person_from(self, connection: sqlite3.Connection, chat: str, uid: str) -> dict | None:
        row = connection.execute(
            "SELECT name FROM people WHERE chat_id=? AND user_id=?",
            (chat, uid),
        ).fetchone()
        if row is None:
            return None
        messages = connection.execute(
            """SELECT message_id, text, timestamp_json FROM messages
               WHERE chat_id=? AND user_id=?
               ORDER BY (timestamp_epoch IS NULL), timestamp_epoch, seq""",
            (chat, uid),
        ).fetchall()
        return {
            "id": uid,
            "name": row["name"],
            "messages": [
                {
                    "id": message["message_id"],
                    "text": message["text"],
                    "timestamp": self._decode_timestamp(message["timestamp_json"]),
                }
                for message in messages
            ],
        }

    def people(self, chat_id: str) -> dict:
        chat, _ = self._identity(chat_id)
        with self._open() as connection:
            rows = connection.execute(
                "SELECT user_id FROM people WHERE chat_id=? ORDER BY user_id",
                (chat,),
            ).fetchall()
            return {
                row["user_id"]: self._person_from(connection, chat, row["user_id"])
                for row in rows
            }

    def person(self, chat_id: str, uid: str) -> dict | None:
        chat, user = self._identity(chat_id, uid)
        assert user is not None
        with self._open() as connection:
            return self._person_from(connection, chat, user)

    def snapshot(self, chat_id: str, uid: str) -> dict:
        chat, user = self._identity(chat_id, uid)
        assert user is not None
        with self._open() as connection:
            connection.execute("BEGIN")
            person = self._person_from(connection, chat, user)
            watermark = connection.execute(
                """SELECT COALESCE(MAX(seq), 0) AS through_seq,
                          COALESCE(MAX(timestamp_epoch), 0.0) AS latest_timestamp
                   FROM observations WHERE chat_id=? AND user_id=?""",
                (chat, user),
            ).fetchone()
            return {
                "person": person,
                "through_seq": int(watermark["through_seq"]),
                "latest_timestamp": float(watermark["latest_timestamp"]),
            }

    def _state(self, connection: sqlite3.Connection, chat: str, uid: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT baseline_seq, latest_timestamp, inclusive FROM classification_state WHERE chat_id=? AND user_id=?",
            (chat, uid),
        ).fetchone()

    def _apply_baseline(
        self,
        connection: sqlite3.Connection,
        chat: str,
        uid: str,
        through_seq: int,
        latest: float,
    ) -> bool:
        existing = self._state(connection, chat, uid)
        if existing is not None:
            old_seq = int(existing["baseline_seq"])
            old_latest = float(existing["latest_timestamp"])
            if through_seq < old_seq or (through_seq == old_seq and latest <= old_latest):
                return False
            latest = max(latest, old_latest)
        connection.execute(
            """INSERT INTO classification_state
               (chat_id, user_id, baseline_seq, latest_timestamp, inclusive)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                   baseline_seq=excluded.baseline_seq,
                   latest_timestamp=excluded.latest_timestamp,
                   inclusive=1""",
            (chat, uid, through_seq, latest),
        )
        connection.execute(
            """UPDATE observations SET pending=0
               WHERE chat_id=? AND user_id=? AND seq<=?""",
            (chat, uid, through_seq),
        )
        connection.execute(
            """DELETE FROM observations
               WHERE chat_id=? AND user_id=? AND timestamp_epoch IS NOT NULL AND timestamp_epoch<?""",
            (chat, uid, latest),
        )
        return True

    def new_count(self, chat_id: str, uid: str, profile: dict | None = None) -> int:
        chat, user = self._identity(chat_id, uid)
        assert user is not None
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM people WHERE chat_id=? AND user_id=?",
                (chat, user),
            ).fetchone()
            if exists is None:
                return 0
            state = self._state(connection, chat, user)
            recovered = _profile_baseline(profile)
            if recovered is not None and (
                state is None
                or recovered[0] > state["baseline_seq"]
                or (recovered[0] == state["baseline_seq"] and recovered[1] > state["latest_timestamp"])
            ):
                self._apply_baseline(connection, chat, user, *recovered)
                state = self._state(connection, chat, user)
            if state is None and _valid_profile(profile):
                cutoff = _timestamp(profile.get("updated_at"))
                if cutoff is not None:
                    # CLI timestamps have minute precision. Treat the profile's
                    # whole update minute as potentially new rather than losing
                    # later same-minute message IDs.
                    cutoff = math.floor(cutoff / 60.0) * 60.0
                    connection.execute(
                        """INSERT INTO classification_state
                           (chat_id, user_id, baseline_seq, latest_timestamp, inclusive)
                           VALUES (?, ?, 0, ?, 1)""",
                        (chat, user, cutoff),
                    )
                    # Bootstrap excludes older history while retaining the cutoff
                    # minute because minute-granularity IDs may have arrived later.
                    connection.execute(
                        "DELETE FROM observations WHERE chat_id=? AND user_id=? AND timestamp_epoch<?",
                        (chat, user, cutoff),
                    )
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM observations
                   WHERE chat_id=? AND user_id=? AND pending=1""",
                (chat, user),
            ).fetchone()
            return int(row["count"])

    def needs_classification(self, chat_id: str, uid: str, profile: dict | None) -> bool:
        person = self.person(chat_id, uid)
        if not person or not person["messages"]:
            return False
        if not _valid_profile(profile):
            return True
        chat, user = self._identity(chat_id, uid)
        assert user is not None
        with self._open() as connection:
            has_state = self._state(connection, chat, user) is not None
        if not has_state and _timestamp(profile.get("updated_at")) is None:
            return True
        return self.new_count(chat, user, profile) > 10

    def mark_classified(self, chat_id: str, uid: str, snapshot: dict) -> None:
        chat, user = self._identity(chat_id, uid)
        assert user is not None
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be a dictionary")
        through_seq = snapshot.get("through_seq")
        latest = snapshot.get("latest_timestamp")
        if isinstance(through_seq, bool) or not isinstance(through_seq, int) or through_seq < 0:
            raise ValueError("snapshot through_seq must be a non-negative integer")
        if isinstance(latest, bool) or not isinstance(latest, (int, float)) or not math.isfinite(latest):
            raise ValueError("snapshot latest_timestamp must be a finite number")
        latest = float(latest)
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._state(connection, chat, user)
            if existing is not None and through_seq < existing["baseline_seq"]:
                return
            person = snapshot.get("person")
            name = person.get("name") if isinstance(person, dict) else None
            name = name.strip() if isinstance(name, str) and name.strip() else user
            connection.execute(
                """INSERT INTO people(chat_id, user_id, name) VALUES (?, ?, ?)
                   ON CONFLICT(chat_id, user_id) DO UPDATE SET name=excluded.name""",
                (chat, user, name),
            )
            self._apply_baseline(connection, chat, user, through_seq, latest)

    def clear_chat(self, chat_id: str) -> None:
        chat, _ = self._identity(chat_id)
        with self._open() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM people WHERE chat_id=?", (chat,))


__all__ = ["MessageCache"]
