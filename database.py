import sqlite3
from datetime import datetime

from flask import session
from runtime_paths import ensure_runtime_copy


def _get_db_name(dept=None):
    if not dept:
        try:
            dept = session.get("department", "DEFAULT")
        except RuntimeError:
            dept = "DEFAULT"
    return ensure_runtime_copy(f"timetable_{dept}.db")


def connect(dept=None):
    db_name = _get_db_name(dept)
    conn = sqlite3.connect(db_name)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _normalize_text(value):
    return (value or "").strip()


def _normalize_year(value):
    return _normalize_text(value).upper()


def _normalize_division(value):
    return _normalize_text(value).upper()


def _normalize_subject_type(value):
    value = _normalize_text(value).upper()
    return "LAB" if value == "LAB" else "LEC"


def _normalize_room_type(value):
    value = _normalize_text(value).upper()
    return "Lab" if value == "LAB" else "CR"


def _normalize_section_type(value):
    value = _normalize_text(value).upper()
    return "LAB" if value == "LAB" else "LEC"


def _default_weekly_hours(subject_type):
    return 2 if _normalize_subject_type(subject_type) == "LAB" else 4


def _normalize_weekly_hours(value, subject_type):
    if value in {None, ""}:
        return _default_weekly_hours(subject_type)

    try:
        hours = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Weekly hours must be a whole number.") from exc

    if hours < 1:
        raise ValueError("Weekly hours must be at least 1.")

    return hours


def _split_class_name(class_name):
    class_name = _normalize_text(class_name)
    if "-" not in class_name:
        return None, None
    year, division = class_name.split("-", 1)
    return _normalize_year(year), _normalize_division(division)


def _year_sort_sql(alias="s"):
    return (
        f"CASE {alias}.year "
        "WHEN 'SE' THEN 1 "
        "WHEN 'TE' THEN 2 "
        "WHEN 'BE' THEN 3 "
        "ELSE 99 END"
    )


def _table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _ensure_column(conn, table_name, column_name, definition):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")


def _get_or_create_teacher(conn, full_name, short_code):
    full_name = _normalize_text(full_name)
    short_code = _normalize_text(short_code).upper()

    if not full_name or not short_code:
        raise ValueError("Teacher name and code are required.")

    row = conn.execute(
        "SELECT id FROM teachers WHERE short_code = ?",
        (short_code,),
    ).fetchone()

    if row:
        teacher_id = row[0]
        conn.execute(
            "UPDATE teachers SET full_name = ? WHERE id = ?",
            (full_name, teacher_id),
        )
        return teacher_id

    cursor = conn.execute(
        "INSERT INTO teachers (full_name, short_code) VALUES (?, ?)",
        (full_name, short_code),
    )
    return cursor.lastrowid


def _upsert_subject(conn, name, teacher_id, year, division, subject_type="LEC", weekly_hours=None):
    name = _normalize_text(name)
    year = _normalize_year(year)
    division = _normalize_division(division)
    subject_type = _normalize_subject_type(subject_type)
    weekly_hours = _normalize_weekly_hours(weekly_hours, subject_type)

    if not name or not teacher_id or not year or not division:
        raise ValueError("Subject name, teacher, year, and division are required.")

    row = conn.execute(
        """
        SELECT id
        FROM subjects
        WHERE name = ? AND teacher_id = ? AND year = ? AND division = ? AND subject_type = ?
        """,
        (name, teacher_id, year, division, subject_type),
    ).fetchone()

    if row:
        subject_id = row[0]
        conn.execute(
            """
            UPDATE subjects
            SET weekly_hours = ?
            WHERE id = ?
            """,
            (weekly_hours, subject_id),
        )
        return subject_id

    cursor = conn.execute(
        """
        INSERT INTO subjects (name, teacher_id, year, division, subject_type, weekly_hours)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, teacher_id, year, division, subject_type, weekly_hours),
    )
    return cursor.lastrowid


def _cleanup_orphan_teacher(conn, teacher_id):
    conn.execute(
        """
        DELETE FROM teachers
        WHERE id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM subjects
              WHERE teacher_id = ?
          )
        """,
        (teacher_id, teacher_id),
    )


def _resolve_teacher_id(conn, entry):
    teacher_id = entry.get("teacher_id")
    if teacher_id:
        return teacher_id

    teacher_name = _normalize_text(entry.get("teacher"))
    if not teacher_name:
        return None

    row = conn.execute(
        "SELECT id FROM teachers WHERE full_name || ' (' || short_code || ')' = ?",
        (teacher_name,),
    ).fetchone()
    return row[0] if row else None


def _resolve_subject_id(conn, entry):
    subject_id = entry.get("subject_id")
    if subject_id:
        return subject_id

    subject_name = _normalize_text(entry.get("subject"))
    if not subject_name:
        return None

    class_name = entry.get("class")
    year, division = _split_class_name(class_name)
    teacher_name = _normalize_text(entry.get("teacher"))

    if year and division:
        row = conn.execute(
            """
            SELECT s.id
            FROM subjects s
            JOIN teachers t ON s.teacher_id = t.id
            WHERE s.name = ? AND s.year = ? AND s.division = ?
            ORDER BY CASE WHEN t.full_name || ' (' || t.short_code || ')' = ? THEN 0 ELSE 1 END,
                     s.id
            LIMIT 1
            """,
            (subject_name, year, division, teacher_name),
        ).fetchone()
        if row:
            return row[0]

    row = conn.execute(
        "SELECT id FROM subjects WHERE name = ? ORDER BY id LIMIT 1",
        (subject_name,),
    ).fetchone()
    return row[0] if row else None


def _resolve_classroom_id(conn, entry):
    classroom_id = entry.get("classroom_id")
    if classroom_id:
        return classroom_id

    room_name = _normalize_text(entry.get("room"))
    if not room_name:
        return None

    row = conn.execute(
        "SELECT id FROM classrooms WHERE room_name = ?",
        (room_name,),
    ).fetchone()
    return row[0] if row else None


def _seed_default_classrooms(conn):
    for room_number in range(101, 111):
        conn.execute(
            """
            INSERT OR IGNORE INTO classrooms (room_name, room_type)
            VALUES (?, ?)
            """,
            (f"CR-{room_number}", "CR"),
        )

    for lab_number in range(1, 13):
        conn.execute(
            """
            INSERT OR IGNORE INTO classrooms (room_name, room_type)
            VALUES (?, ?)
            """,
            (f"Lab-{lab_number}", "Lab"),
        )


def _seed_admin_user(conn, dept=None):
    row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not row:
        from werkzeug.security import generate_password_hash
        password = f"{dept.lower()}123" if dept and dept != "DEFAULT" else "admin123"
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash(password), "admin")
        )

def _seed_demo_teacher(conn, dept=None):
    row = conn.execute("SELECT id FROM users WHERE username = 'teacher1'").fetchone()
    if not row:
        from werkzeug.security import generate_password_hash
        # First ensure a teacher exists in teachers table
        t_row = conn.execute("SELECT id FROM teachers LIMIT 1").fetchone()
        t_id = t_row[0] if t_row else None
        
        password = "password123"
        conn.execute(
            "INSERT INTO users (username, password_hash, role, teacher_id) VALUES (?, ?, ?, ?)",
            ("teacher1", generate_password_hash(password), "teacher", t_id)
        )

def create_tables(dept="DEFAULT"):
    conn = connect(dept)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                short_code TEXT NOT NULL UNIQUE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                teacher_id INTEGER NOT NULL,
                year TEXT NOT NULL,
                division TEXT NOT NULL,
                subject_type TEXT NOT NULL DEFAULT 'LEC',
                weekly_hours INTEGER NOT NULL DEFAULT 4,
                FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE
            )
            """
        )


        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                teacher_id INTEGER,
                FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE SET NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teacher_availability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                slot TEXT NOT NULL,
                is_available BOOLEAN NOT NULL DEFAULT 1,
                FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
                UNIQUE (teacher_id, day, slot)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS classrooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name TEXT NOT NULL UNIQUE,
                room_type TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS class_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_name TEXT NOT NULL UNIQUE,
                year TEXT NOT NULL,
                division TEXT NOT NULL,
                section_type TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS timetable_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                slot TEXT NOT NULL,
                class_name TEXT NOT NULL,
                subject_id INTEGER,
                teacher_id INTEGER,
                classroom_id INTEGER,
                entry_type TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id),
                FOREIGN KEY (teacher_id) REFERENCES teachers(id),
                FOREIGN KEY (classroom_id) REFERENCES classrooms(id)
            )
            """
        )

        _ensure_column(conn, "subjects", "subject_type", "subject_type TEXT NOT NULL DEFAULT 'LEC'")
        _ensure_column(conn, "subjects", "weekly_hours", "weekly_hours INTEGER NOT NULL DEFAULT 4")
        _ensure_column(conn, "teachers", "full_name", "full_name TEXT")
        _ensure_column(conn, "users", "role", "role TEXT NOT NULL DEFAULT 'admin'")
        _ensure_column(conn, "users", "teacher_id", "teacher_id INTEGER REFERENCES teachers(id) ON DELETE SET NULL")
        _seed_default_classrooms(conn)
        _seed_admin_user(conn, dept)
        _seed_demo_teacher(conn, dept)

        conn.commit()
    finally:
        conn.close()


def add_teacher(full_name, short_code, subject_name=None, year=None, division=None, subject_type="LEC", weekly_hours=None):
    conn = connect()
    try:
        teacher_id = _get_or_create_teacher(conn, full_name, short_code)

        if subject_name and year and division:
            _upsert_subject(
                conn,
                subject_name,
                teacher_id,
                year,
                division,
                subject_type=subject_type,
                weekly_hours=weekly_hours,
            )

        conn.commit()
        return teacher_id
    finally:
        conn.close()


def update_teacher_assignment(
    assignment_id,
    full_name,
    short_code,
    subject_name,
    year,
    division,
    subject_type="LEC",
    weekly_hours=None,
):
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT teacher_id, weekly_hours, subject_type
            FROM subjects
            WHERE id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if not row:
            raise ValueError("Teaching assignment not found.")

        old_teacher_id, existing_weekly_hours, existing_subject_type = row
        teacher_id = _get_or_create_teacher(conn, full_name, short_code)

        subject_name = _normalize_text(subject_name)
        year = _normalize_year(year)
        division = _normalize_division(division)
        subject_type = _normalize_subject_type(subject_type)

        if not subject_name or not year or not division:
            raise ValueError("Subject name, year, and division are required.")

        if weekly_hours in {None, ""}:
            if _normalize_subject_type(existing_subject_type) == subject_type:
                resolved_weekly_hours = existing_weekly_hours
            else:
                resolved_weekly_hours = _default_weekly_hours(subject_type)
        else:
            resolved_weekly_hours = _normalize_weekly_hours(weekly_hours, subject_type)

        duplicate = conn.execute(
            """
            SELECT id
            FROM subjects
            WHERE id != ?
              AND name = ?
              AND teacher_id = ?
              AND year = ?
              AND division = ?
              AND subject_type = ?
            LIMIT 1
            """,
            (assignment_id, subject_name, teacher_id, year, division, subject_type),
        ).fetchone()
        if duplicate:
            raise ValueError("A matching teaching assignment already exists.")

        conn.execute(
            """
            UPDATE subjects
            SET name = ?, teacher_id = ?, year = ?, division = ?, subject_type = ?, weekly_hours = ?
            WHERE id = ?
            """,
            (subject_name, teacher_id, year, division, subject_type, resolved_weekly_hours, assignment_id),
        )

        if old_teacher_id != teacher_id:
            _cleanup_orphan_teacher(conn, old_teacher_id)

        conn.commit()
    finally:
        conn.close()


def get_teacher_records():
    conn = connect()
    try:
        data = conn.execute(
            """
            SELECT id, full_name, short_code
            FROM teachers
            ORDER BY full_name, short_code
            """
        ).fetchall()
        return data
    finally:
        conn.close()


def get_teachers():
    conn = connect()
    try:
        data = conn.execute(
            f"""
            SELECT
                s.id,
                t.full_name,
                t.short_code,
                s.name,
                s.year,
                s.division,
                s.subject_type,
                s.weekly_hours,
                t.id,
                COALESCE(
                    (
                        SELECT cs1.section_name
                        FROM class_sections cs1
                        WHERE cs1.year = s.year
                          AND cs1.division = s.division
                          AND cs1.section_type = s.subject_type
                        ORDER BY cs1.id
                        LIMIT 1
                    ),
                    (
                        SELECT cs2.section_name
                        FROM class_sections cs2
                        WHERE cs2.year = s.year
                          AND cs2.division = s.division
                        ORDER BY
                            CASE cs2.section_type
                                WHEN 'LEC' THEN 0
                                WHEN 'LAB' THEN 1
                                ELSE 2
                            END,
                            cs2.id
                        LIMIT 1
                    ),
                    s.year || '-' || s.division
                ) AS section_name
            FROM subjects s
            JOIN teachers t ON s.teacher_id = t.id
            ORDER BY {_year_sort_sql('s')}, s.division, s.name, t.full_name
            """
        ).fetchall()
        return data
    finally:
        conn.close()


def delete_teacher(teacher_or_assignment_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, teacher_id FROM subjects WHERE id = ?",
            (teacher_or_assignment_id,),
        ).fetchone()

        if row:
            subject_id, teacher_id = row
            conn.execute("DELETE FROM timetable_entries WHERE subject_id = ?", (subject_id,))
            conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
            _cleanup_orphan_teacher(conn, teacher_id)
        else:
            conn.execute("DELETE FROM timetable_entries WHERE teacher_id = ?", (teacher_or_assignment_id,))
            conn.execute("DELETE FROM subjects WHERE teacher_id = ?", (teacher_or_assignment_id,))
            conn.execute("DELETE FROM teachers WHERE id = ?", (teacher_or_assignment_id,))

        conn.commit()
    finally:
        conn.close()


def add_subject(name, teacher_id, year, division, subject_type="LEC", weekly_hours=None):
    conn = connect()
    try:
        subject_id = _upsert_subject(
            conn,
            name,
            teacher_id,
            year,
            division,
            subject_type=subject_type,
            weekly_hours=weekly_hours,
        )
        conn.commit()
        return subject_id
    finally:
        conn.close()


def get_subjects():
    conn = connect()
    try:
        data = conn.execute(
            f"""
            SELECT
                s.id,
                s.name,
                s.year,
                s.division,
                s.subject_type,
                s.weekly_hours,
                t.full_name,
                t.short_code,
                t.id
            FROM subjects s
            JOIN teachers t ON s.teacher_id = t.id
            ORDER BY {_year_sort_sql('s')}, s.division, s.name
            """
        ).fetchall()
        return data
    finally:
        conn.close()


def get_subjects_by_year(year):
    conn = connect()
    try:
        data = conn.execute(
            """
            SELECT
                s.id,
                s.name,
                s.year,
                s.division,
                s.subject_type,
                s.weekly_hours,
                t.full_name,
                t.short_code,
                t.id
            FROM subjects s
            JOIN teachers t ON s.teacher_id = t.id
            WHERE s.year = ?
            ORDER BY s.division, s.name
            """,
            (_normalize_year(year),),
        ).fetchall()
        return data
    finally:
        conn.close()


def get_subjects_for_scheduler():
    conn = connect()
    try:
        data = conn.execute(
            f"""
            SELECT
                s.id,
                s.name,
                s.year,
                s.division,
                s.subject_type,
                s.weekly_hours,
                t.id,
                t.full_name,
                t.short_code,
                (
                    SELECT cs.section_name
                    FROM class_sections cs
                    WHERE cs.year = s.year
                      AND cs.division = s.division
                      AND cs.section_type = s.subject_type
                    ORDER BY cs.id
                    LIMIT 1
                ) AS section_name
            FROM subjects s
            JOIN teachers t ON s.teacher_id = t.id
            ORDER BY {_year_sort_sql('s')}, s.division, s.name, t.full_name
            """
        ).fetchall()
        return data
    finally:
        conn.close()


def delete_subject(subject_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT teacher_id FROM subjects WHERE id = ?",
            (subject_id,),
        ).fetchone()

        conn.execute("DELETE FROM timetable_entries WHERE subject_id = ?", (subject_id,))
        conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))

        if row:
            _cleanup_orphan_teacher(conn, row[0])

        conn.commit()
    finally:
        conn.close()


def add_class_section(section_name, year, division, section_type):
    section_name = _normalize_text(section_name)
    year = _normalize_year(year)
    division = _normalize_division(division)
    section_type = _normalize_section_type(section_type)

    if not section_name:
        raise ValueError("Section name is required.")
    if not year:
        raise ValueError("Year is required.")
    if not division:
        raise ValueError("Division is required.")

    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM class_sections WHERE section_name = ?",
            (section_name,),
        ).fetchone()

        if row:
            conn.execute(
                "UPDATE class_sections SET year = ?, division = ?, section_type = ? WHERE id = ?",
                (year, division, section_type, row[0]),
            )
            section_id = row[0]
        else:
            cursor = conn.execute(
                """
                INSERT INTO class_sections (section_name, year, division, section_type)
                VALUES (?, ?, ?, ?)
                """,
                (section_name, year, division, section_type),
            )
            section_id = cursor.lastrowid

        conn.commit()
        return section_id
    finally:
        conn.close()


def update_class_section(section_id, section_name, year, division, section_type):
    section_name = _normalize_text(section_name)
    year = _normalize_year(year)
    division = _normalize_division(division)
    section_type = _normalize_section_type(section_type)

    if not section_name:
        raise ValueError("Section name is required.")
    if not year:
        raise ValueError("Year is required.")
    if not division:
        raise ValueError("Division is required.")

    conn = connect()
    try:
        conn.execute(
            """
            UPDATE class_sections
            SET section_name = ?, year = ?, division = ?, section_type = ?
            WHERE id = ?
            """,
            (section_name, year, division, section_type, section_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_class_sections():
    conn = connect()
    try:
        data = conn.execute(
            """
            SELECT id, section_name, year, division, section_type
            FROM class_sections
            ORDER BY year, division, section_name
            """
        ).fetchall()
        return data
    finally:
        conn.close()


def delete_class_section(section_id):
    conn = connect()
    try:
        conn.execute("DELETE FROM class_sections WHERE id = ?", (section_id,))
        conn.commit()
    finally:
        conn.close()


def add_classroom(room_name, room_type):
    room_name = _normalize_text(room_name)
    room_type = _normalize_room_type(room_type)

    if not room_name:
        raise ValueError("Classroom name is required.")

    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM classrooms WHERE room_name = ?",
            (room_name,),
        ).fetchone()

        if row:
            conn.execute(
                "UPDATE classrooms SET room_type = ? WHERE id = ?",
                (room_type, row[0]),
            )
            classroom_id = row[0]
        else:
            cursor = conn.execute(
                """
                INSERT INTO classrooms (room_name, room_type)
                VALUES (?, ?)
                """,
                (room_name, room_type),
            )
            classroom_id = cursor.lastrowid

        conn.commit()
        return classroom_id
    finally:
        conn.close()


def get_classrooms():
    conn = connect()
    try:
        data = conn.execute(
            """
            SELECT id, room_name, room_type
            FROM classrooms
            ORDER BY room_type, room_name
            """
        ).fetchall()
        return data
    finally:
        conn.close()


def get_classrooms_by_type(room_type):
    conn = connect()
    try:
        data = conn.execute(
            """
            SELECT id, room_name, room_type
            FROM classrooms
            WHERE room_type = ?
            ORDER BY room_name
            """,
            (_normalize_text(room_type),),
        ).fetchall()
        return data
    finally:
        conn.close()


def delete_classroom(classroom_id):
    conn = connect()
    try:
        conn.execute("DELETE FROM timetable_entries WHERE classroom_id = ?", (classroom_id,))
        conn.execute("DELETE FROM classrooms WHERE id = ?", (classroom_id,))
        conn.commit()
    finally:
        conn.close()


def save_timetable(master):
    conn = connect()
    try:
        conn.execute("DELETE FROM timetable_entries")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for entry in master:
            entry_type = _normalize_text(entry.get("type")).lower() or "lec"

            if entry_type in {"break", "free"}:
                conn.execute(
                    """
                    INSERT INTO timetable_entries
                        (day, slot, class_name, entry_type, generated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry.get("day"),
                        entry.get("slot"),
                        entry.get("class"),
                        entry_type,
                        now,
                    ),
                )
                continue

            conn.execute(
                """
                INSERT INTO timetable_entries
                    (day, slot, class_name, subject_id, teacher_id, classroom_id, entry_type, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("day"),
                    entry.get("slot"),
                    entry.get("class"),
                    _resolve_subject_id(conn, entry),
                    _resolve_teacher_id(conn, entry),
                    _resolve_classroom_id(conn, entry),
                    entry_type,
                    now,
                ),
            )

        conn.commit()
    finally:
        conn.close()


def load_timetable():
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                te.day,
                te.slot,
                te.class_name,
                s.name,
                t.full_name,
                t.short_code,
                cr.room_name,
                te.entry_type,
                s.id,
                t.id,
                cr.id
            FROM timetable_entries te
            LEFT JOIN subjects s ON te.subject_id = s.id
            LEFT JOIN teachers t ON te.teacher_id = t.id
            LEFT JOIN classrooms cr ON te.classroom_id = cr.id
            ORDER BY te.id
            """
        ).fetchall()

        master = []
        for row in rows:
            day, slot, class_name, subject, teacher_name, short_code, room, entry_type, subject_id, teacher_id, classroom_id = row

            if entry_type == "break":
                master.append(
                    {
                        "day": day,
                        "slot": slot,
                        "class": class_name,
                        "type": "break",
                    }
                )
                continue

            if entry_type == "free":
                master.append(
                    {
                        "day": day,
                        "slot": slot,
                        "class": class_name,
                        "subject": None,
                        "teacher": None,
                        "room": None,
                        "type": "free",
                    }
                )
                continue

            master.append(
                {
                    "day": day,
                    "slot": slot,
                    "class": class_name,
                    "subject": subject,
                    "teacher": f"{teacher_name} ({short_code})" if teacher_name else None,
                    "room": room,
                    "type": entry_type,
                    "subject_id": subject_id,
                    "teacher_id": teacher_id,
                    "classroom_id": classroom_id,
                }
            )

        return master
    finally:
        conn.close()


def seed_classrooms(dept=None):
    conn = connect(dept)
    try:
        _seed_default_classrooms(conn)
        _seed_admin_user(conn, dept)
        conn.commit()
    finally:
        conn.close()


def seed_demo_data():
    demo_assignments = [
        ("Prof. Asha Kulkarni", "AK", "Discrete Mathematics", "SE", "A", "LEC", 4),
        ("Prof. Rohan Patil", "RP", "Python Programming", "SE", "A", "LEC", 4),
        ("Prof. Neha Joshi", "NJ", "Python Programming", "SE", "A", "LAB", 2),
        ("Prof. Vivek More", "VM", "Digital Logic", "SE", "A", "LEC", 4),
        ("Prof. Simran Kale", "SK", "Web Technology", "SE", "A", "LEC", 4),
        ("Prof. Simran Kale", "SK", "Web Technology", "SE", "A", "LAB", 2),
        ("Prof. Meera Shah", "MS", "Data Structures", "SE", "B", "LEC", 4),
        ("Prof. Omkar Deshmukh", "OD", "Computer Graphics", "SE", "B", "LEC", 4),
        ("Prof. Kiran Jadhav", "KJ", "Computer Graphics", "SE", "B", "LAB", 2),
        ("Prof. Priya Nair", "PN", "Engineering Mathematics", "SE", "B", "LEC", 4),
        ("Prof. Abhay Salunke", "AS", "Database Management Systems", "TE", "A", "LEC", 4),
        ("Prof. Abhay Salunke", "AS", "Database Management Systems", "TE", "A", "LAB", 2),
        ("Prof. Leena Rao", "LR", "Operating Systems", "TE", "A", "LEC", 4),
        ("Prof. Tanmay Kulkarni", "TK", "Computer Networks", "TE", "A", "LEC", 4),
        ("Prof. Pooja Iyer", "PI", "Software Engineering", "TE", "B", "LEC", 4),
        ("Prof. Yash Mehta", "YM", "Java Programming", "TE", "B", "LEC", 4),
        ("Prof. Yash Mehta", "YM", "Java Programming", "TE", "B", "LAB", 2),
        ("Prof. Rutuja Gokhale", "RG", "Theory of Computation", "TE", "B", "LEC", 4),
        ("Prof. Devansh Joshi", "DJ", "Artificial Intelligence", "BE", "A", "LEC", 4),
        ("Prof. Kavya Gupta", "KG", "Machine Learning", "BE", "A", "LEC", 4),
        ("Prof. Kavya Gupta", "KG", "Machine Learning", "BE", "A", "LAB", 2),
        ("Prof. Manasi Apte", "MA", "Cloud Computing", "BE", "A", "LEC", 4),
        ("Prof. Nikhil Singh", "NS", "Cyber Security", "BE", "B", "LEC", 4),
        ("Prof. Trisha Rao", "TR", "Big Data Analytics", "BE", "B", "LEC", 4),
        ("Prof. Trisha Rao", "TR", "Big Data Analytics", "BE", "B", "LAB", 2),
        ("Prof. Harsh Kulkarni", "HK", "Project Management", "BE", "B", "LEC", 3),
    ]
    demo_rooms = [
        ("Seminar Hall", "CR"),
        ("Project Studio", "CR"),
        ("AI-Lab", "Lab"),
        ("Innovation-Lab", "Lab"),
    ]

    seed_classrooms()

    for room_name, room_type in demo_rooms:
        add_classroom(room_name, room_type)

    for full_name, short_code, subject_name, year, division, subject_type, weekly_hours in demo_assignments:
        add_teacher(
            full_name,
            short_code,
            subject_name,
            year,
            division,
            subject_type,
            weekly_hours=weekly_hours,
        )


DEPARTMENTS = ["ENTC", "CS", "IT", "CIVIL", "MECHANICAL"]

def initialize_all_departments():
    for dept in DEPARTMENTS:
        create_tables(dept)
        seed_classrooms(dept)

if __name__ == "__main__":
    initialize_all_departments()

def get_user_by_username(username, dept=None):
    conn = connect(dept)
    try:
        row = conn.execute("SELECT id, username, password_hash, role, teacher_id FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            return {"id": row[0], "username": row[1], "password_hash": row[2], "role": row[3], "teacher_id": row[4]}
        return None
    finally:
        conn.close()

def get_teacher_availability(teacher_id):
    conn = connect()
    try:
        data = conn.execute("SELECT day, slot, is_available FROM teacher_availability WHERE teacher_id = ?", (teacher_id,)).fetchall()
        return [{"day": row[0], "slot": row[1], "is_available": bool(row[2])} for row in data]
    finally:
        conn.close()

def set_teacher_availability(teacher_id, day, slot, is_available):
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO teacher_availability (teacher_id, day, slot, is_available)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(teacher_id, day, slot) DO UPDATE SET is_available = excluded.is_available
            """,
            (teacher_id, day, slot, int(is_available))
        )
        conn.commit()
    finally:
        conn.close()
