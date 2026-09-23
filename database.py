"""
Слой доступа к данным (SQLite через aiosqlite).

Одно долгоживущее соединение на весь процесс: SQLite прекрасно с этим
справляется при небольшой нагрузке, а WAL-режим позволяет читать
параллельно с записью.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Пользователи бота (роль + привязка к группе или преподавателю)
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    full_name    TEXT,
    role         TEXT    NOT NULL DEFAULT 'student',
    group_id     INTEGER REFERENCES groups(id)   ON DELETE SET NULL,
    teacher_id   INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
    subscribed   INTEGER NOT NULL DEFAULT 1,   -- получать объявления
    is_blocked   INTEGER NOT NULL DEFAULT 0,   -- пользователь заблокировал бота
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

-- Учебные группы / классы
CREATE TABLE IF NOT EXISTS groups (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    sort  INTEGER NOT NULL DEFAULT 0
);

-- Преподаватели
CREATE TABLE IF NOT EXISTS teachers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    subject   TEXT,
    phone     TEXT,
    room      TEXT,
    email     TEXT
);

-- Расписание: одна строка = один урок в конкретный день недели
CREATE TABLE IF NOT EXISTS lessons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   INTEGER NOT NULL REFERENCES groups(id)   ON DELETE CASCADE,
    teacher_id INTEGER          REFERENCES teachers(id) ON DELETE SET NULL,
    weekday    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    number     INTEGER NOT NULL,          -- номер урока
    time_start TEXT    NOT NULL,          -- 'ЧЧ:ММ'
    time_end   TEXT    NOT NULL,
    subject    TEXT    NOT NULL,
    room       TEXT,
    UNIQUE (group_id, weekday, number)
);
CREATE INDEX IF NOT EXISTS idx_lessons_group   ON lessons(group_id, weekday);
CREATE INDEX IF NOT EXISTS idx_lessons_teacher ON lessons(teacher_id, weekday);

-- Меню столовой: одна строка = одно блюдо
CREATE TABLE IF NOT EXISTS dishes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday   INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    meal_type TEXT    NOT NULL,           -- breakfast / lunch / snack
    name      TEXT    NOT NULL,
    sort      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dishes_day ON dishes(weekday, meal_type);

-- Объявления
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id  INTEGER NOT NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    sent_count INTEGER NOT NULL DEFAULT 0
);

-- Часто задаваемые вопросы
CREATE TABLE IF NOT EXISTS faq (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer   TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0
);
"""


def _now() -> str:
    """Текущее время в ISO-формате (для служебных полей)."""
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Асинхронная обёртка над SQLite со всеми CRUD-операциями."""

    def __init__(self, path: str | Path | None = None) -> None:
        # Путь можно задать позже через configure(): так глобальный экземпляр
        # создаётся при импорте модуля, а настраивается уже в post_init.
        self._path: str | None = str(path) if path is not None else None
        self._conn: aiosqlite.Connection | None = None

    def configure(self, path: str | Path) -> None:
        """Задаёт путь к файлу БД до вызова connect()."""
        self._path = str(path)

    # --- Жизненный цикл ---------------------------------------------------
    async def connect(self) -> None:
        """Открывает соединение и создаёт схему, если её ещё нет."""
        if self._path is None:
            raise RuntimeError("Путь к БД не задан: вызовите init_db(path)")
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("База данных готова: %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Соединение с БД закрыто")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    # --- Низкоуровневые помощники -----------------------------------------
    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Выполняет запрос с коммитом, возвращает lastrowid."""
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid or 0

    # --- Пользователи ------------------------------------------------------
    async def upsert_user(
        self, user_id: int, username: str | None, full_name: str
    ) -> None:
        """Создаёт пользователя или обновляет его профиль при каждом /start."""
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
            (user_id, username, full_name, _now(), _now()),
        )

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))

    async def set_user_role(
        self,
        user_id: int,
        role: str,
        group_id: int | None = None,
        teacher_id: int | None = None,
    ) -> None:
        await self._execute(
            """UPDATE users
                  SET role = ?, group_id = ?, teacher_id = ?, updated_at = ?
                WHERE user_id = ?""",
            (role, group_id, teacher_id, _now(), user_id),
        )

    async def set_subscription(self, user_id: int, subscribed: bool) -> None:
        await self._execute(
            "UPDATE users SET subscribed = ?, updated_at = ? WHERE user_id = ?",
            (int(subscribed), _now(), user_id),
        )

    async def mark_blocked(self, user_id: int) -> None:
        """Пользователь заблокировал бота — исключаем из будущих рассылок."""
        await self._execute(
            "UPDATE users SET is_blocked = 1, updated_at = ? WHERE user_id = ?",
            (_now(), user_id),
        )

    async def get_broadcast_targets(self) -> list[int]:
        rows = await self._fetchall(
            "SELECT user_id FROM users WHERE subscribed = 1 AND is_blocked = 0"
        )
        return [r["user_id"] for r in rows]

    async def get_stats(self) -> dict[str, int]:
        row = await self._fetchone(
            """
            SELECT
                (SELECT COUNT(*) FROM users)                             AS users_total,
                (SELECT COUNT(*) FROM users WHERE role = 'student')      AS students,
                (SELECT COUNT(*) FROM users WHERE role = 'teacher')      AS teachers_users,
                (SELECT COUNT(*) FROM users WHERE subscribed = 1
                                              AND is_blocked = 0)        AS subscribed,
                (SELECT COUNT(*) FROM users WHERE is_blocked = 1)        AS blocked,
                (SELECT COUNT(*) FROM lessons)                           AS lessons,
                (SELECT COUNT(*) FROM dishes)                            AS dishes,
                (SELECT COUNT(*) FROM announcements)                     AS announcements
            """
        )
        return dict(row) if row else {}

    # --- Группы ------------------------------------------------------------
    async def get_groups(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM groups ORDER BY sort, name")

    async def get_group(self, group_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))

    async def add_group(self, name: str, sort: int = 0) -> int:
        return await self._execute(
            "INSERT OR IGNORE INTO groups (name, sort) VALUES (?, ?)", (name, sort)
        )

    # --- Преподаватели -----------------------------------------------------
    async def get_teachers(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM teachers ORDER BY full_name")

    async def get_teacher(self, teacher_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM teachers WHERE id = ?", (teacher_id,))

    async def add_teacher(
        self,
        full_name: str,
        subject: str | None = None,
        phone: str | None = None,
        room: str | None = None,
        email: str | None = None,
    ) -> int:
        return await self._execute(
            """INSERT INTO teachers (full_name, subject, phone, room, email)
               VALUES (?, ?, ?, ?, ?)""",
            (full_name, subject, phone, room, email),
        )

    # --- Расписание --------------------------------------------------------
    async def get_lessons_by_group(
        self, group_id: int, weekday: int | None = None
    ) -> list[aiosqlite.Row]:
        sql = """
            SELECT l.*, t.full_name AS teacher_name, g.name AS group_name
              FROM lessons l
              LEFT JOIN teachers t ON t.id = l.teacher_id
              JOIN groups g        ON g.id = l.group_id
             WHERE l.group_id = ?
        """
        params: list[Any] = [group_id]
        if weekday is not None:
            sql += " AND l.weekday = ?"
            params.append(weekday)
        sql += " ORDER BY l.weekday, l.number"
        return await self._fetchall(sql, params)

    async def get_lessons_by_teacher(
        self, teacher_id: int, weekday: int | None = None
    ) -> list[aiosqlite.Row]:
        sql = """
            SELECT l.*, t.full_name AS teacher_name, g.name AS group_name
              FROM lessons l
              JOIN teachers t ON t.id = l.teacher_id
              JOIN groups g   ON g.id = l.group_id
             WHERE l.teacher_id = ?
        """
        params: list[Any] = [teacher_id]
        if weekday is not None:
            sql += " AND l.weekday = ?"
            params.append(weekday)
        sql += " ORDER BY l.weekday, l.time_start"
        return await self._fetchall(sql, params)

    async def add_lesson(
        self,
        group_id: int,
        weekday: int,
        number: int,
        time_start: str,
        time_end: str,
        subject: str,
        room: str | None = None,
        teacher_id: int | None = None,
    ) -> int:
        return await self._execute(
            """INSERT OR REPLACE INTO lessons
               (group_id, teacher_id, weekday, number, time_start, time_end, subject, room)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (group_id, teacher_id, weekday, number, time_start, time_end, subject, room),
        )

    # --- Меню столовой -----------------------------------------------------
    async def get_dishes(
        self, weekday: int, meal_type: str | None = None
    ) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM dishes WHERE weekday = ?"
        params: list[Any] = [weekday]
        if meal_type:
            sql += " AND meal_type = ?"
            params.append(meal_type)
        sql += " ORDER BY meal_type, sort, id"
        return await self._fetchall(sql, params)

    async def get_dish(self, dish_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM dishes WHERE id = ?", (dish_id,))

    async def add_dish(self, weekday: int, meal_type: str, name: str) -> int:
        row = await self._fetchone(
            "SELECT COALESCE(MAX(sort), 0) + 1 AS s FROM dishes "
            "WHERE weekday = ? AND meal_type = ?",
            (weekday, meal_type),
        )
        sort = row["s"] if row else 1
        return await self._execute(
            "INSERT INTO dishes (weekday, meal_type, name, sort) VALUES (?, ?, ?, ?)",
            (weekday, meal_type, name.strip(), sort),
        )

    async def update_dish(self, dish_id: int, name: str) -> None:
        await self._execute(
            "UPDATE dishes SET name = ? WHERE id = ?", (name.strip(), dish_id)
        )

    async def delete_dish(self, dish_id: int) -> None:
        await self._execute("DELETE FROM dishes WHERE id = ?", (dish_id,))

    async def clear_meal(self, weekday: int, meal_type: str) -> None:
        await self._execute(
            "DELETE FROM dishes WHERE weekday = ? AND meal_type = ?",
            (weekday, meal_type),
        )

    # --- Объявления --------------------------------------------------------
    async def add_announcement(self, author_id: int, text: str) -> int:
        return await self._execute(
            "INSERT INTO announcements (author_id, text, created_at) VALUES (?, ?, ?)",
            (author_id, text, _now()),
        )

    async def set_announcement_sent(self, ann_id: int, count: int) -> None:
        await self._execute(
            "UPDATE announcements SET sent_count = ? WHERE id = ?", (count, ann_id)
        )

    async def get_announcements(self, limit: int = 5) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT * FROM announcements ORDER BY id DESC LIMIT ?", (limit,)
        )

    # --- FAQ ---------------------------------------------------------------
    async def get_faq(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM faq ORDER BY sort, id")

    async def get_faq_item(self, item_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM faq WHERE id = ?", (item_id,))

    # --- Первичное наполнение ---------------------------------------------
    async def is_empty(self) -> bool:
        row = await self._fetchone("SELECT COUNT(*) AS c FROM groups")
        return (row["c"] if row else 0) == 0

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        await self.conn.executemany(sql, list(rows))
        await self.conn.commit()


# Единственный экземпляр на всё приложение.
# Объект создаётся при импорте модуля и НИКОГДА не пересоздаётся: иначе
# обработчики, сделавшие `from database import db`, останутся со ссылкой
# на старый объект.
db = Database()


def init_db(path: str | Path) -> Database:
    """Настраивает глобальный объект БД (вызывается один раз в bot.py)."""
    db.configure(path)
    return db
