"""
Слой доступа к данным (SQLite через aiosqlite).

Одно долгоживущее соединение на весь процесс. PTB по умолчанию обрабатывает
апдейты последовательно, поэтому гонок между обработчиками нет.
"""
from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

import config

logger = logging.getLogger(__name__)

# Увеличивайте при несовместимых изменениях схемы
SCHEMA_VERSION = 2

SCHEMA = """
-- Пользователи бота
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    role        TEXT    NOT NULL DEFAULT 'guest',  -- guest / student / teacher / admin
    verified    INTEGER NOT NULL DEFAULT 0,        -- 1 = вошёл по коду приглашения
    group_id    INTEGER REFERENCES groups(id)   ON DELETE SET NULL,
    teacher_id  INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
    subscribed  INTEGER NOT NULL DEFAULT 1,
    is_blocked  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_teacher ON users(teacher_id);

-- Классы
CREATE TABLE IF NOT EXISTS groups (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    sort  INTEGER NOT NULL DEFAULT 0
);

-- Преподаватели
CREATE TABLE IF NOT EXISTS teachers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE,
    subject   TEXT,
    phone     TEXT,
    room      TEXT,
    email     TEXT
);

-- Расписание. Без UNIQUE (класс, день, номер): урок может делиться на подгруппы
CREATE TABLE IF NOT EXISTS lessons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   INTEGER NOT NULL REFERENCES groups(id)   ON DELETE CASCADE,
    teacher_id INTEGER          REFERENCES teachers(id) ON DELETE SET NULL,
    weekday    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    number     INTEGER NOT NULL,
    time_start TEXT    NOT NULL,
    time_end   TEXT    NOT NULL,
    subject    TEXT    NOT NULL,
    room       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lessons_group   ON lessons(group_id, weekday, number);
CREATE INDEX IF NOT EXISTS idx_lessons_teacher ON lessons(teacher_id, weekday, number);

-- Меню столовой
CREATE TABLE IF NOT EXISTS dishes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday   INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    meal_type TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    sort      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dishes_day ON dishes(weekday, meal_type);

-- Объявления (text хранится в безопасном HTML)
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id  INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    sent_count INTEGER NOT NULL DEFAULT 0
);

-- FAQ
CREATE TABLE IF NOT EXISTS faq (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer   TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0
);

-- Коды приглашения: kind='group' — код класса, kind='teacher' — ссылка учителю
CREATE TABLE IF NOT EXISTS invites (
    code        TEXT PRIMARY KEY,
    kind        TEXT    NOT NULL CHECK (kind IN ('group', 'teacher')),
    group_id    INTEGER REFERENCES groups(id)   ON DELETE CASCADE,
    teacher_id  INTEGER REFERENCES teachers(id) ON DELETE CASCADE,
    max_uses    INTEGER,                  -- NULL = без ограничений
    uses        INTEGER NOT NULL DEFAULT 0,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invites_target ON invites(kind, group_id, teacher_id);

-- Срочные изменения расписания на конкретную дату.
-- Одна строка = один урок класса в этот день; subject = NULL — урок отменён.
CREATE TABLE IF NOT EXISTS schedule_changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT    NOT NULL,                 -- YYYY-MM-DD
    group_id   INTEGER NOT NULL REFERENCES groups(id)   ON DELETE CASCADE,
    number     INTEGER NOT NULL,
    subject    TEXT,
    teacher_id INTEGER          REFERENCES teachers(id) ON DELETE SET NULL,
    time_start TEXT    NOT NULL,
    time_end   TEXT    NOT NULL,
    note       TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    UNIQUE (day, group_id, number)
);
CREATE INDEX IF NOT EXISTS idx_changes_day ON schedule_changes(day, group_id);

"""

# Алфавит без похожих символов (0/O, 1/I/L), чтобы код было легко продиктовать
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def _now() -> str:
    """Текущее время в часовом поясе школы."""
    return datetime.now(config.TIMEZONE).isoformat(timespec="seconds")


def generate_code() -> str:
    """Криптостойкий случайный код приглашения."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class Database:
    """Асинхронная обёртка над SQLite со всеми CRUD-операциями."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path: str | None = str(path) if path is not None else None
        self._conn: aiosqlite.Connection | None = None

    def configure(self, path: str | Path) -> None:
        self._path = str(path)

    # --- Жизненный цикл ---------------------------------------------------
    async def connect(self) -> None:
        if self._path is None:
            raise RuntimeError("Путь к БД не задан: вызовите init_db() до connect()")
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._check_schema_version()
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("БД подключена: %s", self._path)

    async def _check_schema_version(self) -> None:
        """Не даём запуститься на базе старого формата — иначе будут странные ошибки."""
        row = await self._fetchone("PRAGMA user_version")
        version = row[0] if row else 0
        if version == SCHEMA_VERSION:
            return
        existing = await self._fetchone(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        )
        if version == 0 and existing is None:  # новая пустая база
            await self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        raise RuntimeError(
            f"Файл БД {self._path} создан старой версией бота "
            f"(схема v{version}, нужна v{SCHEMA_VERSION}). Если это тестовая база — "
            "удалите её (вместе с файлами -wal и -shm) и запустите бота заново."
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("БД не подключена")
        return self._conn

    # --- Низкоуровневые помощники -----------------------------------------
    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        await self.conn.executemany(sql, list(rows))
        await self.conn.commit()

    # --- Пользователи -----------------------------------------------------
    async def upsert_user(self, user_id: int, username: str | None, full_name: str | None) -> None:
        now = _now()
        await self._execute(
            """
            INSERT INTO users (user_id, username, full_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                full_name  = excluded.full_name,
                is_blocked = 0,
                updated_at = excluded.updated_at
            """,
            (user_id, username, full_name, now, now),
        )

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))

    async def is_verified(self, user_id: int) -> bool:
        row = await self._fetchone("SELECT verified FROM users WHERE user_id = ?", (user_id,))
        return bool(row and row["verified"])

    async def set_profile(
        self, user_id: int, role: str, *, group_id: int | None = None, teacher_id: int | None = None
    ) -> None:
        """Устанавливает роль и привязку, помечая пользователя проверенным."""
        await self._execute(
            "UPDATE users SET role = ?, verified = 1, group_id = ?, teacher_id = ?, "
            "updated_at = ? WHERE user_id = ?",
            (role, group_id, teacher_id, _now(), user_id),
        )

    async def set_group(self, user_id: int, group_id: int) -> None:
        await self._execute(
            "UPDATE users SET group_id = ?, updated_at = ? WHERE user_id = ?",
            (group_id, _now(), user_id),
        )

    async def set_subscription(self, user_id: int, subscribed: bool) -> None:
        await self._execute(
            "UPDATE users SET subscribed = ?, updated_at = ? WHERE user_id = ?",
            (int(subscribed), _now(), user_id),
        )

    async def mark_blocked(self, user_id: int) -> None:
        await self._execute(
            "UPDATE users SET is_blocked = 1, updated_at = ? WHERE user_id = ?",
            (_now(), user_id),
        )

    async def get_broadcast_targets(self) -> list[int]:
        rows = await self._fetchall(
            "SELECT user_id FROM users WHERE verified = 1 AND subscribed = 1 AND is_blocked = 0"
        )
        return [r["user_id"] for r in rows]

    async def get_stats(self) -> dict[str, int]:
        row = await self._fetchone(
            """
            SELECT
              (SELECT COUNT(*) FROM users)                                  AS users_total,
              (SELECT COUNT(*) FROM users WHERE verified = 1)               AS verified,
              (SELECT COUNT(*) FROM users WHERE role = 'student')           AS students,
              (SELECT COUNT(*) FROM users WHERE role = 'teacher')           AS teachers_users,
              (SELECT COUNT(*) FROM users
                 WHERE verified = 1 AND subscribed = 1 AND is_blocked = 0)  AS subscribed,
              (SELECT COUNT(*) FROM users WHERE is_blocked = 1)             AS blocked,
              (SELECT COUNT(*) FROM lessons)                                AS lessons,
              (SELECT COUNT(*) FROM dishes)                                 AS dishes,
              (SELECT COUNT(*) FROM announcements)                          AS announcements
            """
        )
        return dict(row) if row else {}

    # --- Классы -----------------------------------------------------------
    async def get_groups(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM groups ORDER BY sort, name")

    async def get_group(self, group_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))

    async def upsert_group(self, name: str, sort: int = 0) -> int:
        await self._execute(
            "INSERT INTO groups (name, sort) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET sort = excluded.sort",
            (name, sort),
        )
        row = await self._fetchone("SELECT id FROM groups WHERE name = ?", (name,))
        return row["id"]

    # --- Преподаватели ----------------------------------------------------
    async def get_teachers(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM teachers ORDER BY full_name")

    async def get_teacher(self, teacher_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM teachers WHERE id = ?", (teacher_id,))

    async def upsert_teacher(self, full_name: str, subject: str | None = None) -> int:
        """Создаёт преподавателя или обновляет предметы; телефон/почта не трогаются."""
        await self._execute(
            "INSERT INTO teachers (full_name, subject) VALUES (?, ?) "
            "ON CONFLICT(full_name) DO UPDATE SET subject = excluded.subject",
            (full_name, subject),
        )
        row = await self._fetchone("SELECT id FROM teachers WHERE full_name = ?", (full_name,))
        return row["id"]

    async def get_teacher_user(self, teacher_id: int) -> aiosqlite.Row | None:
        """Telegram-аккаунт, привязанный к преподавателю."""
        return await self._fetchone(
            "SELECT * FROM users WHERE teacher_id = ? LIMIT 1", (teacher_id,)
        )

    async def get_linked_teacher_ids(self) -> set[int]:
        rows = await self._fetchall(
            "SELECT DISTINCT teacher_id FROM users WHERE teacher_id IS NOT NULL"
        )
        return {r["teacher_id"] for r in rows}

    async def unlink_teacher(self, teacher_id: int) -> None:
        """Отвязывает аккаунт от преподавателя и отзывает его ссылки."""
        now = _now()
        try:
            # Обычный преподаватель теряет доступ; админ — только привязку
            await self.conn.execute(
                "UPDATE users SET role = 'guest', verified = 0, teacher_id = NULL, "
                "updated_at = ? WHERE teacher_id = ? AND role = 'teacher'",
                (now, teacher_id),
            )
            await self.conn.execute(
                "UPDATE users SET teacher_id = NULL, updated_at = ? WHERE teacher_id = ?",
                (now, teacher_id),
            )
            await self.conn.execute(
                "UPDATE invites SET revoked = 1 "
                "WHERE kind = 'teacher' AND teacher_id = ? AND revoked = 0",
                (teacher_id,),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    # --- Расписание -------------------------------------------------------
    _LESSON_SELECT = """
        SELECT l.*, t.full_name AS teacher_name, g.name AS group_name, g.sort AS group_sort
        FROM lessons l
        JOIN groups g        ON g.id = l.group_id
        LEFT JOIN teachers t ON t.id = l.teacher_id
    """

    async def get_lessons_by_group(self, group_id: int, weekday: int | None = None) -> list[aiosqlite.Row]:
        if weekday is None:
            return await self._fetchall(
                self._LESSON_SELECT + " WHERE l.group_id = ? ORDER BY l.weekday, l.number, l.id",
                (group_id,),
            )
        return await self._fetchall(
            self._LESSON_SELECT + " WHERE l.group_id = ? AND l.weekday = ? ORDER BY l.number, l.id",
            (group_id, weekday),
        )

    async def get_lessons_by_teacher(self, teacher_id: int, weekday: int | None = None) -> list[aiosqlite.Row]:
        if weekday is None:
            return await self._fetchall(
                self._LESSON_SELECT
                + " WHERE l.teacher_id = ? ORDER BY l.weekday, l.number, g.sort",
                (teacher_id,),
            )
        return await self._fetchall(
            self._LESSON_SELECT
            + " WHERE l.teacher_id = ? AND l.weekday = ? ORDER BY l.number, g.sort",
            (teacher_id, weekday),
        )

    async def replace_schedule(self, lessons: Iterable[Sequence[Any]]) -> None:
        """
        Атомарно заменяет всё расписание.
        Кортеж: (group_id, teacher_id, weekday, number, time_start, time_end, subject, room)
        """
        try:
            await self.conn.execute("DELETE FROM lessons")
            await self.conn.executemany(
                "INSERT INTO lessons (group_id, teacher_id, weekday, number, "
                "time_start, time_end, subject, room) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                list(lessons),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    # --- Срочные изменения расписания -------------------------------------
    _CHANGE_SELECT = """
                     SELECT c.*, t.full_name AS teacher_name, g.name AS group_name, g.sort AS group_sort
                     FROM schedule_changes c
                              JOIN groups g ON g.id = c.group_id
                              LEFT JOIN teachers t ON t.id = c.teacher_id \
                     """

    async def get_changes(self, day: str, group_id: int | None = None) -> list[aiosqlite.Row]:
        """Изменения на дату (YYYY-MM-DD): по всем классам или по одному."""
        if group_id is None:
            return await self._fetchall(
                self._CHANGE_SELECT + " WHERE c.day = ? ORDER BY c.number, g.sort", (day,)
            )
        return await self._fetchall(
            self._CHANGE_SELECT + " WHERE c.day = ? AND c.group_id = ? ORDER BY c.number",
            (day, group_id),
        )

    async def get_change(self, change_id: int) -> aiosqlite.Row | None:
        return await self._fetchone(self._CHANGE_SELECT + " WHERE c.id = ?", (change_id,))

    async def get_change_days(self, from_day: str) -> set[str]:
        rows = await self._fetchall(
            "SELECT DISTINCT day FROM schedule_changes WHERE day >= ?", (from_day,)
        )
        return {r["day"] for r in rows}

    async def save_changes(
            self, day: str, group_id: int, items: Sequence[tuple], created_by: int
    ) -> None:
        """
        Атомарно сохраняет изменения.
        items: (number, subject | None, teacher_id | None, time_start, time_end, note)
        """
        now = _now()
        try:
            await self.conn.executemany(
                """
                INSERT INTO schedule_changes (day, group_id, number, subject, teacher_id,
                                              time_start, time_end, note, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(day, group_id, number) DO
                UPDATE SET
                    subject = excluded.subject,
                    teacher_id = excluded.teacher_id,
                    time_start = excluded.time_start,
                    time_end = excluded.time_end,
                    note = excluded.note,
                    created_by = excluded.created_by,
                    created_at = excluded.created_at
                """,
                [
                    (day, group_id, n, subj, tid, ts, te, note, created_by, now)
                    for n, subj, tid, ts, te, note in items
                ],
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def delete_change(self, change_id: int) -> None:
        await self._execute("DELETE FROM schedule_changes WHERE id = ?", (change_id,))

    async def clear_changes(self, day: str, group_id: int) -> None:
        await self._execute(
            "DELETE FROM schedule_changes WHERE day = ? AND group_id = ?", (day, group_id)
        )

    async def get_bells(self) -> dict[int, tuple[str, str]]:
        """Сетка звонков, вычисленная из расписания: номер урока -> (начало, конец)."""
        rows = await self._fetchall(
            "SELECT number, MIN(time_start) AS s, MIN(time_end) AS e FROM lessons GROUP BY number"
        )
        return {r["number"]: (r["s"], r["e"]) for r in rows}

    async def get_group_audience(self, group_id: int) -> list[int]:
        rows = await self._fetchall(
            "SELECT user_id FROM users WHERE group_id = ? AND verified = 1 "
            "AND subscribed = 1 AND is_blocked = 0",
            (group_id,),
        )
        return [r["user_id"] for r in rows]

    async def get_teacher_audience(self, teacher_ids: Iterable[int]) -> list[int]:
        ids = list(teacher_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))  # только плейсхолдеры, значения передаются отдельно
        rows = await self._fetchall(
            f"SELECT user_id FROM users WHERE teacher_id IN ({marks}) AND verified = 1 "
            "AND subscribed = 1 AND is_blocked = 0",
            ids,
        )
        return [r["user_id"] for r in rows]

    # --- Меню столовой ----------------------------------------------------
    async def get_dishes(self, weekday: int, meal_type: str | None = None) -> list[aiosqlite.Row]:
        if meal_type is None:
            return await self._fetchall(
                "SELECT * FROM dishes WHERE weekday = ? ORDER BY sort, id", (weekday,)
            )
        return await self._fetchall(
            "SELECT * FROM dishes WHERE weekday = ? AND meal_type = ? ORDER BY sort, id",
            (weekday, meal_type),
        )

    async def has_dishes(self) -> bool:
        row = await self._fetchone("SELECT COUNT(*) AS c FROM dishes")
        return bool(row and row["c"])

    async def get_dish(self, dish_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM dishes WHERE id = ?", (dish_id,))

    async def add_dish(self, weekday: int, meal_type: str, name: str) -> int:
        row = await self._fetchone(
            "SELECT COALESCE(MAX(sort), 0) + 1 AS s FROM dishes WHERE weekday = ? AND meal_type = ?",
            (weekday, meal_type),
        )
        cur = await self._execute(
            "INSERT INTO dishes (weekday, meal_type, name, sort) VALUES (?, ?, ?, ?)",
            (weekday, meal_type, name, row["s"]),
        )
        return cur.lastrowid

    async def delete_dish(self, dish_id: int) -> None:
        await self._execute("DELETE FROM dishes WHERE id = ?", (dish_id,))

    async def clear_meal(self, weekday: int, meal_type: str) -> None:
        await self._execute(
            "DELETE FROM dishes WHERE weekday = ? AND meal_type = ?", (weekday, meal_type)
        )

    # --- Объявления -------------------------------------------------------
    async def add_announcement(self, author_id: int, text: str) -> int:
        cur = await self._execute(
            "INSERT INTO announcements (author_id, text, created_at) VALUES (?, ?, ?)",
            (author_id, text, _now()),
        )
        return cur.lastrowid

    async def set_announcement_sent(self, ann_id: int, count: int) -> None:
        await self._execute(
            "UPDATE announcements SET sent_count = ? WHERE id = ?", (count, ann_id)
        )

    async def get_announcements(self, limit: int = 5) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT * FROM announcements ORDER BY id DESC LIMIT ?", (limit,)
        )

    # --- FAQ --------------------------------------------------------------
    async def get_faq(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM faq ORDER BY sort, id")

    async def get_faq_item(self, item_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM faq WHERE id = ?", (item_id,))

    async def replace_faq(self, items: Sequence[tuple[str, str]]) -> None:
        try:
            await self.conn.execute("DELETE FROM faq")
            await self.conn.executemany(
                "INSERT INTO faq (question, answer, sort) VALUES (?, ?, ?)",
                [(q, a, i) for i, (q, a) in enumerate(items, start=1)],
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise


    # --- Дополнительно: админ, слияние педагогов, импорт меню -------------
    async def ensure_admin(self, user_id: int) -> None:
        await self._execute(
            "UPDATE users SET role = 'admin', verified = 1, updated_at = ? "
            "WHERE user_id = ? AND (role <> 'admin' OR verified = 0)",
            (_now(), user_id),
        )

    async def update_teacher(self, teacher_id: int, full_name: str, subject: str | None) -> None:
        try:
            await self._execute(
                "UPDATE teachers SET full_name = ?, subject = ? WHERE id = ?",
                (full_name, subject, teacher_id),
            )
        except sqlite3.IntegrityError:  # такое ФИО уже занято — меняем только предмет
            await self._execute(
                "UPDATE teachers SET subject = ? WHERE id = ?", (subject, teacher_id)
            )

    async def merge_teachers(self, keep_id: int, drop_id: int) -> None:
        """Сливает дубль: все ссылки переходят к keep_id, пустые контакты дополняются."""
        try:
            for table in ("lessons", "schedule_changes", "users", "invites"):  # фиксированный список
                await self.conn.execute(
                    f"UPDATE {table} SET teacher_id = ? WHERE teacher_id = ?", (keep_id, drop_id)
                )
            await self.conn.execute(
                """
                UPDATE teachers SET
                    phone = COALESCE(phone, (SELECT phone FROM teachers WHERE id = ?)),
                    room  = COALESCE(room,  (SELECT room  FROM teachers WHERE id = ?)),
                    email = COALESCE(email, (SELECT email FROM teachers WHERE id = ?))
                WHERE id = ?
                """,
                (drop_id, drop_id, drop_id, keep_id),
            )
            await self.conn.execute("DELETE FROM teachers WHERE id = ?", (drop_id,))
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def replace_menu(self, menu) -> None:
        """menu: {weekday: {meal_type: [блюда]}}. Дни из menu заменяются целиком."""
        rows = [
            (wd, meal, name, i)
            for wd, meals in menu.items()
            for meal, names in meals.items()
            for i, name in enumerate(names, start=1)
        ]
        try:
            for wd in menu:
                await self.conn.execute("DELETE FROM dishes WHERE weekday = ?", (wd,))
            await self.conn.executemany(
                "INSERT INTO dishes (weekday, meal_type, name, sort) VALUES (?, ?, ?, ?)", rows
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise




    # --- Коды приглашения -------------------------------------------------
    async def revoke_invites(self, kind: str, obj_id: int) -> None:
        # Имя колонки выбирается из фиксированного набора — не из ввода пользователя
        column = "group_id" if kind == "group" else "teacher_id"
        await self._execute(
            f"UPDATE invites SET revoked = 1 WHERE kind = ? AND {column} = ? AND revoked = 0",
            (kind, obj_id),
        )

    async def create_invite(
        self, kind: str, obj_id: int, created_by: int, max_uses: int | None = None
    ) -> str:
        """Создаёт новый код; прежние коды этого класса/преподавателя отзываются."""
        await self.revoke_invites(kind, obj_id)
        group_id = obj_id if kind == "group" else None
        teacher_id = obj_id if kind == "teacher" else None
        for _ in range(10):
            code = generate_code()
            try:
                await self._execute(
                    "INSERT INTO invites (code, kind, group_id, teacher_id, max_uses, "
                    "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (code, kind, group_id, teacher_id, max_uses, created_by, _now()),
                )
                return code
            except sqlite3.IntegrityError:
                continue  # крайне редкая коллизия — пробуем другой код
        raise RuntimeError("Не удалось сгенерировать уникальный код")

    async def get_active_invite(self, kind: str, obj_id: int) -> aiosqlite.Row | None:
        column = "group_id" if kind == "group" else "teacher_id"
        return await self._fetchone(
            f"SELECT * FROM invites WHERE kind = ? AND {column} = ? AND revoked = 0 "
            "AND (max_uses IS NULL OR uses < max_uses) ORDER BY created_at DESC LIMIT 1",
            (kind, obj_id),
        )

    async def redeem_invite(self, code: str, user_id: int) -> aiosqlite.Row | None:
        """
        Проверяет код и применяет его к пользователю.
        Возвращает строку приглашения или None, если код недействителен.
        """
        invite = await self._fetchone(
            "SELECT * FROM invites WHERE code = ? AND revoked = 0 "
            "AND (max_uses IS NULL OR uses < max_uses)",
            (code,),
        )
        if invite is None:
            return None

        now = _now()
        try:
            await self.conn.execute(
                "UPDATE invites SET uses = uses + 1 WHERE code = ?", (code,)
            )
            if invite["kind"] == "group":
                await self.conn.execute(
                    "UPDATE users SET verified = 1, group_id = ?, teacher_id = NULL, "
                    "role = CASE WHEN role = 'admin' THEN 'admin' ELSE 'student' END, "
                    "updated_at = ? WHERE user_id = ?",
                    (invite["group_id"], now, user_id),
                )
            else:
                # Один преподаватель — один аккаунт: прежнюю привязку снимаем
                await self.conn.execute(
                    "UPDATE users SET teacher_id = NULL, "
                    "verified = CASE WHEN role = 'teacher' THEN 0 ELSE verified END, "
                    "role = CASE WHEN role = 'teacher' THEN 'guest' ELSE role END, "
                    "updated_at = ? WHERE teacher_id = ? AND user_id <> ?",
                    (now, invite["teacher_id"], user_id),
                )
                await self.conn.execute(
                    "UPDATE users SET verified = 1, teacher_id = ?, group_id = NULL, "
                    "role = CASE WHEN role = 'admin' THEN 'admin' ELSE 'teacher' END, "
                    "updated_at = ? WHERE user_id = ?",
                    (invite["teacher_id"], now, user_id),
                )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        return invite


# Единственный экземпляр на всё приложение (никогда не пересоздаётся)
db = Database()


def init_db(path: str | Path) -> Database:
    db.configure(path)
    return db
