import os
import webbrowser
from datetime import datetime
from threading import Timer

from functools import wraps
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for, flash, session
from werkzeug.security import check_password_hash
from database import get_user_by_username
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database import (
    add_class_section,
    add_classroom,
    add_teacher,
    create_tables,
    delete_class_section,
    delete_classroom,
    delete_teacher,
    get_class_sections,
    get_classrooms,
    get_subjects,
    get_teacher_records,
    get_teachers,
    load_timetable,
    save_timetable,
    seed_demo_data,
    update_class_section,
    update_teacher_assignment,
)
from runtime_paths import data_path, resource_path
from scheduler import DAYS, FULL_SLOTS, generate_master, get_room_view, get_student_view, get_teacher_view


app = Flask(
    __name__,
    template_folder=str(resource_path("templates")),
    static_folder=str(resource_path("static")),
)
app.secret_key = "timetable_super_secret_key"

create_tables()


YEAR_ORDER = {"SE": 1, "TE": 2, "BE": 3}
DAY_ORDER = {day: index for index, day in enumerate(DAYS)}
SLOT_ORDER = {slot: index for index, slot in enumerate(FULL_SLOTS)}
PDF_PATH = data_path("timetable.pdf")
SLOT_HEADERS = [
    {"slot": "9-10", "label": "9-10", "editable": True},
    {"slot": "10-11", "label": "10-11", "editable": True},
    {"slot": "BREAK", "label": "Break", "editable": False},
    {"slot": "11:15-12:15", "label": "11-12", "editable": True},
    {"slot": "12:15-1:15", "label": "12-1", "editable": True},
    {"slot": "LUNCH", "label": "Lunch", "editable": False},
    {"slot": "2-3", "label": "2-3", "editable": True},
    {"slot": "3-4", "label": "3-4", "editable": True},
]
SLOT_HEADER_MAP = {column["slot"]: column for column in SLOT_HEADERS}



def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def _clear_saved_timetable_state():
    session.pop("timetable_type", None)
    session.pop("timetable_value", None)


def _class_sort_key(class_name):
    if "-" not in class_name:
        return (99, class_name)
    year, division = class_name.split("-", 1)
    return (YEAR_ORDER.get(year, 99), division)


def _master_sort_key(entry):
    return (
        _class_sort_key(entry.get("class", "")),
        DAY_ORDER.get(entry.get("day"), 99),
        SLOT_ORDER.get(entry.get("slot"), 99),
        1 if entry.get("continuation") else 0,
    )


def _teacher_options():
    return [
        {"id": teacher_id, "name": full_name, "code": short_code}
        for teacher_id, full_name, short_code in get_teacher_records()
    ]


def _class_label(row):
    if row[4] and row[5]:
        return f"{row[4]}-{row[5]}"
    return ""


def _student_options(teacher_rows):
    classes = {_class_label(row) for row in teacher_rows if _class_label(row)}
    return sorted(classes, key=_class_sort_key)


def _class_groups(teacher_rows):
    class_counts = {}
    for row in teacher_rows:
        group = _class_label(row)
        if group:
            class_counts[group] = class_counts.get(group, 0) + 1

    return [
        {"group": group, "assignments": count}
        for group, count in sorted(class_counts.items(), key=lambda item: _class_sort_key(item[0]))
    ]


def _room_options():
    return [room_name for _, room_name, _ in get_classrooms()]


def _display_subject_name(subject_name, entry_type):
    if not subject_name:
        return "-"
    if str(entry_type).upper() == "LAB" and not str(subject_name).upper().endswith("LAB"):
        return f"{subject_name} LAB"
    return subject_name


def _entry_code(entry, selected_type):
    if selected_type == "student":
        return entry.get("teacher_code") or ""
    if selected_type in {"teacher", "room"}:
        return entry.get("class") or ""
    return entry.get("teacher_code") or ""


def _default_room_for_type(subject_type):
    requested_type = "Lab" if str(subject_type).upper() == "LAB" else "CR"

    for _, room_name, room_type in get_classrooms():
        if room_type == requested_type:
            return room_name

    return "Lab-1" if requested_type == "Lab" else "CR-101"


def _manual_subject_duration(subject_type):
    return 2 if str(subject_type).lower() == "lab" else 1


def _subject_card_options(selected_type="", selected_value="", current_entries=None):
    subject_rows = get_subjects()

    if selected_type == "student" and selected_value:
        filtered_rows = [
            row
            for row in subject_rows
            if row[2] and row[3] and f"{row[2]}-{row[3]}" == selected_value
        ]
        if filtered_rows:
            subject_rows = filtered_rows

    room_by_subject = {}
    if current_entries:
        for entry in current_entries:
            subject_id = entry.get("subject_id")
            room_name = entry.get("room")
            if subject_id and room_name and subject_id not in room_by_subject and not entry.get("continuation"):
                room_by_subject[subject_id] = room_name

    cards = []
    for subject_id, subject_name, year, division, subject_type, _, teacher_name, short_code, teacher_id in subject_rows:
        cards.append(
            {
                "id": subject_id,
                "subject": _display_subject_name(subject_name, subject_type),
                "teacher_code": short_code,
                "teacher_name": teacher_name,
                "room": room_by_subject.get(subject_id) or _default_room_for_type(subject_type),
                "entry_type": str(subject_type).lower(),
                "duration": _manual_subject_duration(subject_type),
                "class_name": f"{year}-{division}",
            }
        )

    cards.sort(key=lambda card: (card["class_name"], card["subject"], card["teacher_code"]))
    return cards


def _empty_grid():
    timetable = {}
    for day in DAYS:
        timetable[day] = []
        for column in SLOT_HEADERS:
            if column["slot"] == "BREAK":
                timetable[day].append(
                    {
                        "slot": column["slot"],
                        "label": column["label"],
                        "subject": "Break",
                        "code": "",
                        "room": "",
                        "subject_id": "",
                        "teacher": "",
                        "teacher_code": "",
                        "entry_type": "break",
                        "kind": "break",
                        "editable": False,
                        "continuation": False,
                    }
                )
            elif column["slot"] == "LUNCH":
                timetable[day].append(
                    {
                        "slot": column["slot"],
                        "label": column["label"],
                        "subject": "Lunch",
                        "code": "",
                        "room": "",
                        "subject_id": "",
                        "teacher": "",
                        "teacher_code": "",
                        "entry_type": "break",
                        "kind": "lunch",
                        "editable": False,
                        "continuation": False,
                    }
                )
            else:
                timetable[day].append(
                    {
                        "slot": column["slot"],
                        "label": column["label"],
                        "subject": "-",
                        "code": "",
                        "room": "",
                        "subject_id": "",
                        "teacher": "",
                        "teacher_code": "",
                        "entry_type": "free",
                        "kind": "free",
                        "editable": True,
                        "continuation": False,
                    }
                )
    return timetable


def _build_timetable_grid(entries, selected_type="student"):
    if not entries:
        return _empty_grid()

    grouped = {}
    for entry in entries:
        grouped[(entry.get("day"), entry.get("slot"))] = entry

    timetable = {}
    for day in DAYS:
        row = []
        for column in SLOT_HEADERS:
            slot_name = column["slot"]
            slot_entry = grouped.get((day, slot_name))

            if slot_name == "BREAK":
                row.append(
                    {
                        "slot": slot_name,
                        "label": column["label"],
                        "subject": "SHORT BREAK",
                        "code": "",
                        "room": "",
                        "subject_id": "",
                        "teacher": "",
                        "teacher_code": "",
                        "entry_type": "break",
                        "kind": "break",
                        "editable": False,
                        "continuation": False,
                    }
                )
                continue

            if slot_name == "LUNCH":
                row.append(
                    {
                        "slot": slot_name,
                        "label": column["label"],
                        "subject": "LUNCH",
                        "code": "",
                        "room": "",
                        "subject_id": "",
                        "teacher": "",
                        "teacher_code": "",
                        "entry_type": "break",
                        "kind": "lunch",
                        "editable": False,
                        "continuation": False,
                    }
                )
                continue

            if not slot_entry or slot_entry.get("type") == "free":
                row.append(
                    {
                        "slot": slot_name,
                        "label": column["label"],
                        "subject": "-",
                        "code": "",
                        "room": "",
                        "subject_id": "",
                        "teacher": "",
                        "teacher_code": "",
                        "entry_type": "free",
                        "kind": "free",
                        "editable": True,
                        "continuation": False,
                    }
                )
                continue

            is_continuation = bool(slot_entry.get("continuation"))
            subject_label = _display_subject_name(slot_entry.get("subject"), slot_entry.get("type"))
            row.append(
                {
                    "slot": slot_name,
                    "label": column["label"],
                    "subject": subject_label,
                    "code": _entry_code(slot_entry, selected_type),
                    "room": slot_entry.get("room") or "",
                    "subject_id": slot_entry.get("subject_id") or "",
                    "teacher": slot_entry.get("teacher") or "",
                    "teacher_code": slot_entry.get("teacher_code") or "",
                    "entry_type": slot_entry.get("type") or "lec",
                    "kind": slot_entry.get("type") or "lec",
                    "editable": True,
                    "continuation": is_continuation,
                }
            )

        timetable[day] = row

    return timetable


def _selection_heading(selected_value, timetable):
    if timetable and selected_value:
        return f"Timetable for {selected_value}"
    return "Timetable"


def _pdf_heading(selected_type, selected_value):
    if selected_type == "teacher":
        return f"College Timetable Of Teacher ({selected_value})"
    if selected_type == "room":
        return f"College Timetable Of Room ({selected_value})"
    if selected_value:
        return f"College Timetable Of Class ({selected_value})"
    return "College Timetable"


def _teacher_details_rows(entries):
    teacher_lookup = {code: full_name for _, full_name, code in get_teacher_records()}
    rows = []
    seen_codes = set()

    for entry in entries:
        code = entry.get("teacher_code")
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)

        teacher_name = teacher_lookup.get(code)
        if not teacher_name and entry.get("teacher"):
            teacher_name = str(entry["teacher"]).rsplit("(", 1)[0].strip()

        rows.append([code, teacher_name or "-"])

    rows.sort(key=lambda item: item[0])
    return rows


def _slot_cell_text(entry, selected_type):
    if not entry or entry.get("type") == "free":
        return "-"

    subject = _display_subject_name(entry.get("subject"), entry.get("type"))
    code = _entry_code(entry, selected_type)
    room = entry.get("room") or ""
    lines = [subject]

    if code:
        lines.append(code)

    if room:
        lines.append(room)

    return "\n".join(lines)


def _subject_lookup():
    lookup = {}
    for subject_row in get_subjects():
        subject_id, subject_name, _, _, subject_type, _, teacher_name, short_code, teacher_id = subject_row
        lookup[subject_id] = {
            "subject_id": subject_id,
            "subject": subject_name,
            "teacher": f"{teacher_name} ({short_code})",
            "teacher_code": short_code,
            "teacher_id": teacher_id,
            "type": str(subject_type).lower(),
            "duration": _manual_subject_duration(subject_type),
        }
    return lookup


def _merge_class_entries(master_entries, class_name, replacement_entries):
    filtered_entries = [entry for entry in master_entries if entry.get("class") != class_name]
    filtered_entries.extend(replacement_entries)
    filtered_entries.sort(key=_master_sort_key)
    return filtered_entries


def _build_manual_entries(class_name, cells):
    subject_lookup = _subject_lookup()
    cells_by_position = {(cell.get("day"), cell.get("slot")): cell for cell in cells}
    manual_entries = []

    for day in DAYS:
        slot_index = 0
        while slot_index < len(FULL_SLOTS):
            slot_name = FULL_SLOTS[slot_index]

            if slot_name == "BREAK":
                manual_entries.append(
                    {
                        "day": day,
                        "slot": slot_name,
                        "class": class_name,
                        "subject": "SHORT BREAK",
                        "teacher": None,
                        "teacher_code": None,
                        "room": "",
                        "type": "break",
                        "span": 1,
                    }
                )
                slot_index += 1
                continue

            if slot_name == "LUNCH":
                manual_entries.append(
                    {
                        "day": day,
                        "slot": slot_name,
                        "class": class_name,
                        "subject": "LUNCH",
                        "teacher": None,
                        "teacher_code": None,
                        "room": "",
                        "type": "break",
                        "span": 1,
                    }
                )
                slot_index += 1
                continue

            cell = cells_by_position.get((day, slot_name), {})
            subject_id = cell.get("subject_id")

            try:
                subject_id = int(subject_id) if subject_id not in {None, "", 0, "0"} else None
            except (TypeError, ValueError):
                subject_id = None

            if not subject_id or subject_id not in subject_lookup:
                manual_entries.append(
                    {
                        "day": day,
                        "slot": slot_name,
                        "class": class_name,
                        "subject": None,
                        "teacher": None,
                        "teacher_code": None,
                        "room": None,
                        "type": "free",
                        "span": 1,
                    }
                )
                slot_index += 1
                continue

            subject_entry = subject_lookup[subject_id]
            room_name = cell.get("room") or _default_room_for_type(subject_entry["type"])
            span = 1

            if subject_entry["type"] == "lab":
                expected_span = max(1, int(subject_entry.get("duration") or 2))
                next_index = slot_index + 1
                while next_index < len(FULL_SLOTS) and span < expected_span:
                    next_slot = FULL_SLOTS[next_index]
                    if next_slot in {"BREAK", "LUNCH"}:
                        break

                    next_cell = cells_by_position.get((day, next_slot), {})
                    next_subject_id = next_cell.get("subject_id")

                    try:
                        next_subject_id = int(next_subject_id) if next_subject_id not in {None, "", 0, "0"} else None
                    except (TypeError, ValueError):
                        next_subject_id = None

                    if next_subject_id != subject_id:
                        break

                    span += 1
                    next_index += 1

            manual_entries.append(
                {
                    "day": day,
                    "slot": slot_name,
                    "class": class_name,
                    "subject_id": subject_entry["subject_id"],
                    "subject": subject_entry["subject"],
                    "teacher_id": subject_entry["teacher_id"],
                    "teacher": subject_entry["teacher"],
                    "teacher_code": subject_entry["teacher_code"],
                    "room": room_name,
                    "type": subject_entry["type"],
                    "span": span,
                }
            )

            for offset in range(1, span):
                continuation_slot = FULL_SLOTS[slot_index + offset]
                manual_entries.append(
                    {
                        "day": day,
                        "slot": continuation_slot,
                        "class": class_name,
                        "subject_id": subject_entry["subject_id"],
                        "subject": subject_entry["subject"],
                        "teacher_id": subject_entry["teacher_id"],
                        "teacher": subject_entry["teacher"],
                        "teacher_code": subject_entry["teacher_code"],
                        "room": room_name,
                        "type": subject_entry["type"],
                        "span": 0,
                        "continuation": True,
                    }
                )

            slot_index += span

    manual_entries.sort(key=_master_sort_key)
    return manual_entries


def _teachers_context():
    teacher_rows = get_teachers()
    return {
        "teachers": teacher_rows,
        "classrooms": get_classrooms(),
        "class_sections": get_class_sections(),
        "classes": _class_groups(teacher_rows),
        "active_page": "teachers",
    }


def _extract_teacher_code(entry):
    code = (entry.get("teacher_code") or "").strip()
    if code:
        return code

    teacher_name = str(entry.get("teacher") or "")
    if "(" in teacher_name and ")" in teacher_name:
        return teacher_name.rsplit("(", 1)[1].rstrip(")").strip()

    return ""


def _latest_master_for_dashboard():
    return load_timetable()


def _missing_mapping_keys(teacher_rows, class_sections):
    needed_keys = {
        (_normalize_year_division_value(row[4]), _normalize_year_division_value(row[5]), str(row[6] or "").upper())
        for row in teacher_rows
        if row[4] and row[5] and row[6]
    }
    mapped_keys = {
        (_normalize_year_division_value(row[2]), _normalize_year_division_value(row[3]), str(row[4] or "").upper())
        for row in class_sections
        if row[2] and row[3] and row[4]
    }
    return sorted(needed_keys - mapped_keys)


def _normalize_year_division_value(value):
    return str(value or "").strip().upper()


def _today_rows(master_entries):
    day_name = datetime.now().strftime("%A")
    if day_name not in DAYS:
        return day_name, []

    rows = []
    for entry in master_entries:
        if entry.get("day") != day_name:
            continue
        if entry.get("type") in {"break", "free"}:
            continue
        if entry.get("continuation"):
            continue

        rows.append(
            {
                "class_name": entry.get("class") or "-",
                "slot": entry.get("slot") or "-",
                "subject": _display_subject_name(entry.get("subject"), entry.get("type")),
                "teacher_code": _extract_teacher_code(entry) or "-",
                "room": entry.get("room") or "-",
            }
        )

    rows.sort(key=lambda item: (_class_sort_key(item["class_name"]), SLOT_ORDER.get(item["slot"], 99)))
    return day_name, rows


def _dashboard_context():
    teacher_rows = get_teachers()
    class_sections = get_class_sections()
    master_entries = _latest_master_for_dashboard()
    todays_day, today_view_rows = _today_rows(master_entries)
    missing_mapping_keys = _missing_mapping_keys(teacher_rows, class_sections)
    non_free_entries = [entry for entry in master_entries if entry.get("type") not in {"break", "free"}]

    alerts = []
    if not teacher_rows:
        alerts.append("No teaching assignments found. Add teacher data first.")
    if missing_mapping_keys:
        missing_preview = ", ".join([f"{year}-{division} ({entry_type})" for year, division, entry_type in missing_mapping_keys[:4]])
        suffix = " ..." if len(missing_mapping_keys) > 4 else ""
        alerts.append(f"Room mapping missing for: {missing_preview}{suffix}")
    if not non_free_entries:
        alerts.append("No generated timetable found yet. Generate once from Timetable page.")

    stat_cards = [
        {"label": "Assignments", "value": len(teacher_rows)},
        {"label": "Teachers", "value": len(get_teacher_records())},
        {"label": "Classes", "value": len(_class_groups(teacher_rows))},
        {"label": "Classrooms", "value": len(get_classrooms())},
        {"label": "Mapped Class Types", "value": len(class_sections)},
        {"label": "Scheduled Slots", "value": len(non_free_entries)},
    ]

    return {
        "assignment_count": len(teacher_rows),
        "teacher_count": len(get_teacher_records()),
        "class_count": len(_class_groups(teacher_rows)),
        "classroom_count": len(get_classrooms()),
        "stats": stat_cards,
        "alerts": alerts,
        "today_label": todays_day,
        "today_rows": today_view_rows,
        "active_page": "dashboard",
    }


def _timetable_context(timetable=None, selected_type="", selected_value=""): 
    teacher_rows = get_teachers()
    grid = timetable or _empty_grid()
    current_entries = []
    if timetable and selected_type == "student" and selected_value:
        master = load_timetable()
        current_entries = get_student_view(master, selected_value)

    return {
        "teacher_options": _teacher_options(),
        "student_options": _student_options(teacher_rows),
        "room_options": _room_options(),
        "grid": grid,
        "days": DAYS,
        "slot_headers": SLOT_HEADERS,
        "selected_type": selected_type,
        "selected_value": selected_value,
        "page_heading": _selection_heading(selected_value, timetable),
        "has_timetable": bool(timetable),
        "editable_subjects": _subject_card_options(selected_type, selected_value, current_entries=current_entries),
        "active_page": "timetable",
    }


@app.route("/")
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        user = get_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        
        flash("Invalid username or password.", "warning")
        return redirect(url_for("login"))

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", **_dashboard_context())


@app.route("/teachers")
@login_required
def teachers_page():
    selected_section = request.args.get("section", "teachers")
    if selected_section not in {"teachers", "classes"}:
        selected_section = "teachers"

    context = _teachers_context()
    context["selected_section"] = selected_section

    return render_template(
        "teachers.html",
        **context,
    )


@app.route("/add_teacher", methods=["POST"])
@login_required
def add_teacher_route():
    assignment_id = request.form.get("assignment_id")

    try:
        if assignment_id:
            update_teacher_assignment(
                int(assignment_id),
                request.form["name"],
                request.form["code"],
                request.form["subject"],
                request.form["year"],
                request.form["division"],
                request.form["type"],
                weekly_hours=request.form.get("weekly_hours"),
            )
            flash("Teaching assignment updated successfully.", "success")
        else:
            add_teacher(
                request.form["name"],
                request.form["code"],
                request.form["subject"],
                request.form["year"],
                request.form["division"],
                request.form["type"],
                weekly_hours=request.form.get("weekly_hours"),
            )
            flash("Teaching assignment added successfully.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("teachers_page"))

    _clear_saved_timetable_state()
    return redirect(url_for("teachers_page"))


@app.route("/delete_teacher/<int:assignment_id>")
@login_required
def delete_teacher_route(assignment_id):
    delete_teacher(assignment_id)
    _clear_saved_timetable_state()
    flash("Teaching assignment deleted.", "success")
    return redirect(url_for("teachers_page"))


@app.route("/add_classroom", methods=["POST"])
@login_required
def add_classroom_route():
    try:
        add_classroom(
            request.form["room_name"],
            request.form["room_type"],
        )
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("teachers_page"))

    _clear_saved_timetable_state()
    flash("Classroom saved successfully.", "success")
    return redirect(url_for("teachers_page"))


@app.route("/save_class_section", methods=["POST"])
@login_required
def save_class_section_route():
    section_id = request.form.get("section_id")
    try:
        if section_id:
            update_class_section(
                int(section_id),
                request.form["section_name"],
                request.form["year"],
                request.form["division"],
                request.form["section_type"],
            )
            message = "Class section updated successfully."
        else:
            add_class_section(
                request.form["section_name"],
                request.form["year"],
                request.form["division"],
                request.form["section_type"],
            )
            message = "Class section added successfully."
    except ValueError as exc:
        return redirect(url_for("teachers_page", error=str(exc)))

    _clear_saved_timetable_state()
    return redirect(url_for("teachers_page", message=message))


@app.route("/delete_class_section/<int:section_id>")
@login_required
def delete_class_section_route(section_id):
    delete_class_section(section_id)
    _clear_saved_timetable_state()
    return redirect(url_for("teachers_page", message="Class section deleted."))


@app.route("/delete_classroom/<int:classroom_id>")
@login_required
def delete_classroom_route(classroom_id):
    delete_classroom(classroom_id)
    _clear_saved_timetable_state()
    flash("Classroom deleted.", "success")
    return redirect(url_for("teachers_page"))


@app.route("/seed_demo_data", methods=["POST"])
@login_required
def seed_demo_data_route():
    seed_demo_data()
    _clear_saved_timetable_state()
    return redirect(url_for("teachers_page", message="Sample data added. You can generate timetables now."))


@app.route("/timetable")
@login_required
def timetable():
    selected_type = session.get("timetable_type", "")
    selected_value = session.get("timetable_value", "")

    master = load_timetable()
    timetable_grid = None

    if master and selected_type and selected_value:
        if selected_type == "student":
            filtered_entries = get_student_view(master, selected_value)
        elif selected_type == "teacher":
            filtered_entries = get_teacher_view(master, selected_value)
        else:
            filtered_entries = get_room_view(master, selected_value)
        
        timetable_grid = _build_timetable_grid(filtered_entries, selected_type)

    return render_template(
        "timetable.html",
        **_timetable_context(
            timetable=timetable_grid,
            selected_type=selected_type,
            selected_value=selected_value,
        ),
    )


@app.route("/generate")
@login_required
def generate():
    global saved_master
    global saved_view_entries
    global saved_grid
    global saved_selection

    selected_type = request.args.get("type", "").strip()
    selected_value = request.args.get("value", "").strip()

    if selected_type not in {"student", "teacher", "room"} or not selected_value:
        return render_template(
            "timetable.html",
            **_timetable_context(
                timetable=saved_grid,
                error="Please select a valid timetable filter first.",
                selected_type=selected_type,
                selected_value=selected_value,
            ),
        )

    generated_new_master = not bool(saved_master)
    master = saved_master or generate_master()
    if selected_type == "student" and master and not any(entry.get("class") == selected_value for entry in master):
        master = generate_master()
        generated_new_master = True

    if not master:
        return render_template(
            "timetable.html",
            **_timetable_context(
                timetable=None,
                error="Add teacher-subject records first, then generate the timetable.",
                selected_type=selected_type,
                selected_value=selected_value,
            ),
        )

    if selected_type == "student":
        filtered_entries = get_student_view(master, selected_value)
    elif selected_type == "teacher":
        filtered_entries = get_teacher_view(master, selected_value)
    else:
        filtered_entries = get_room_view(master, selected_value)

    timetable_grid = _build_timetable_grid(filtered_entries, selected_type)

    saved_master = master
    saved_view_entries = filtered_entries
    saved_grid = timetable_grid
    saved_selection = {"type": selected_type, "value": selected_value}

    if generated_new_master:
        save_timetable(master)

    return render_template(
        "timetable.html",
        **_timetable_context(
            timetable=timetable_grid,
            selected_type=selected_type,
            selected_value=selected_value,
        ),
    )


@app.route("/save_timetable_edits", methods=["POST"])
@login_required
def save_timetable_edits():
    global saved_master
    global saved_view_entries
    global saved_grid
    global saved_selection

    payload = request.get_json(silent=True) or {}
    selected_type = str(payload.get("selected_type") or "").strip()
    selected_value = str(payload.get("selected_value") or "").strip()
    cells = payload.get("cells") or []

    if selected_type != "student" or not selected_value:
        return jsonify({"ok": False, "message": "Manual editing is available for student timetables only."}), 400

    manual_entries = _build_manual_entries(selected_value, cells)
    saved_master = _merge_class_entries(saved_master, selected_value, manual_entries)
    saved_view_entries = get_student_view(saved_master, selected_value)
    saved_grid = _build_timetable_grid(saved_view_entries, selected_type)
    saved_selection = {"type": selected_type, "value": selected_value}

    save_timetable(saved_master)

    return jsonify({"ok": True, "redirect": url_for("timetable")})


@app.route("/download")
@login_required
def download():
    if not saved_view_entries:
        return redirect(url_for("timetable"))

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PdfTitle",
        parent=styles["Title"],
        alignment=1,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#2f241f"),
    )
    section_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#2f241f"),
        fontSize=13,
        leading=16,
        spaceAfter=10,
    )

    document = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=28,
        bottomMargin=24,
    )

    header_row = ["Day"] + [column["label"] for column in SLOT_HEADERS]
    table_data = [header_row]
    span_commands = []
    view_lookup = {(entry.get("day"), entry.get("slot")): entry for entry in saved_view_entries}

    for row_index, day in enumerate(DAYS, start=1):
        row = [day] + [""] * len(FULL_SLOTS)
        for slot_index, slot_name in enumerate(FULL_SLOTS):
            column_index = slot_index + 1

            if slot_name == "BREAK":
                row[column_index] = "SHORT BREAK"
                continue

            if slot_name == "LUNCH":
                row[column_index] = "LUNCH"
                continue

            entry = view_lookup.get((day, slot_name))
            if not entry or entry.get("type") == "free":
                row[column_index] = "-"
                continue

            if entry.get("continuation"):
                row[column_index] = ""
                continue

            row[column_index] = _slot_cell_text(entry, saved_selection["type"])
            span = int(entry.get("span", 1) or 1)
            if span > 1:
                span_commands.append(("SPAN", (column_index, row_index), (column_index + span - 1, row_index)))

        table_data.append(row)

    timetable_table = Table(
        table_data,
        colWidths=[64, 76, 76, 70, 76, 76, 54, 76, 44],
        repeatRows=1,
        hAlign="CENTER",
    )
    timetable_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#a82f2f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
            + span_commands
        )
    )

    elements = [
        Paragraph(_pdf_heading(saved_selection["type"], saved_selection["value"]), title_style),
        Spacer(1, 18),
        timetable_table,
    ]

    if saved_selection["type"] == "student":
        teacher_rows = _teacher_details_rows(saved_view_entries)
        if teacher_rows:
            teacher_table = Table(
                [["Teacher Code", "Full Name"]] + teacher_rows,
                colWidths=[92, 220],
                hAlign="CENTER",
            )
            teacher_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("LEADING", (0, 0), (-1, -1), 11),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )

            elements.extend(
                [
                    Spacer(1, 28),
                    Paragraph("Teacher Details", section_style),
                    Spacer(1, 6),
                    teacher_table,
                ]
            )

    document.build(elements)

    download_name = f"timetable-{saved_selection['value'] or 'view'}.pdf"
    return send_file(str(PDF_PATH), as_attachment=True, download_name=download_name)


@app.route("/manifest.webmanifest")
def manifest():
    response = send_file(
        str(resource_path("static", "manifest.webmanifest")),
        mimetype="application/manifest+json",
        max_age=0,
    )
    response.cache_control.no_cache = True
    return response


@app.route("/service-worker.js")
def service_worker():
    response = send_file(
        str(resource_path("static", "service-worker.js")),
        mimetype="application/javascript",
        max_age=0,
    )
    response.cache_control.no_cache = True
    return response


def _read_port():
    raw_value = str(os.environ.get("TIMETABLE_PORT", "5000")).strip()
    try:
        port = int(raw_value)
    except ValueError:
        return 5000

    if 1 <= port <= 65535:
        return port
    return 5000


def _read_host():
    host = str(os.environ.get("TIMETABLE_HOST", "127.0.0.1")).strip()
    return host or "127.0.0.1"


def _setting_enabled(name, default=False):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _open_browser(host, port):
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    webbrowser.open_new(f"http://{browser_host}:{port}/")


if __name__ == "__main__":
    host = _read_host()
    port = _read_port()
    debug_enabled = _setting_enabled("TIMETABLE_DEBUG", default=False)

    if _setting_enabled("TIMETABLE_OPEN_BROWSER", default=True):
        Timer(1.2, _open_browser, args=(host, port)).start()

    app.run(host=host, port=port, debug=debug_enabled, use_reloader=False)
