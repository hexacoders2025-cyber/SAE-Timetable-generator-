import math
import random
from collections import defaultdict
from functools import lru_cache

from database import get_classrooms, get_subjects_for_scheduler


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
FULL_SLOTS = ["9-10", "10-11", "BREAK", "11:15-12:15", "12:15-1:15", "LUNCH", "2-3", "3-4"]
BREAK_SLOTS = {"BREAK", "LUNCH"}
TEACHING_SLOT_INDEXES = [index for index, slot in enumerate(FULL_SLOTS) if slot not in BREAK_SLOTS]
TEACHING_SLOT_RANK = {slot_index: rank for rank, slot_index in enumerate(TEACHING_SLOT_INDEXES)}

DEFAULT_ROOM_CHOICES = {
    "CR": [f"CR-{room_number}" for room_number in range(101, 111)],
    "Lab": [f"Lab-{lab_number}" for lab_number in range(1, 13)],
}

POPULATION_SIZE = 32
MAX_GENERATIONS = 80
MUTATION_RATE = 0.1
TOURNAMENT_K = 5
ELITISM_COUNT = 3
STAGNATION_LIMIT = 16
MAX_SAME_SUBJECT_PER_DAY = 2
MAX_TEACHER_SLOTS_PER_DAY = 4
FAST_SCHEDULER_ATTEMPTS = 18
FAST_ACCEPTABLE_PENALTY = 60


def _room_choices():
    room_map = {"CR": [], "Lab": []}

    for _, room_name, room_type in get_classrooms():
        normalized_type = "Lab" if str(room_type).lower() == "lab" else "CR"
        room_map.setdefault(normalized_type, []).append(room_name)

    for room_type, fallback_rooms in DEFAULT_ROOM_CHOICES.items():
        if not room_map.get(room_type):
            room_map[room_type] = fallback_rooms[:]

    return room_map


def _resolve_assigned_room(raw_room, room_type, room_choices):
    value = str(raw_room or "").strip()
    if not value:
        return None

    available_rooms = room_choices.get(room_type) or DEFAULT_ROOM_CHOICES[room_type]
    if not available_rooms:
        return None

    available_lookup = {room.lower(): room for room in available_rooms}
    candidates = [value]
    uppercase_value = value.upper()

    if room_type == "CR":
        if uppercase_value.startswith("CR-"):
            suffix = value.split("-", 1)[1].strip()
            if suffix:
                candidates.append(f"CR-{suffix}")
        elif uppercase_value.startswith("CR"):
            suffix = value[2:].strip(" -")
            if suffix:
                candidates.append(f"CR-{suffix}")

        if value.isdigit():
            candidates.append(f"CR-{value}")

        digits_only = "".join(character for character in value if character.isdigit())
        if digits_only:
            candidates.append(f"CR-{digits_only}")
    else:
        if uppercase_value.startswith("LAB-"):
            suffix = value.split("-", 1)[1].strip()
            if suffix:
                candidates.append(f"Lab-{suffix}")
        elif uppercase_value.startswith("LAB"):
            suffix = value[3:].strip(" -")
            if suffix:
                candidates.append(f"Lab-{suffix}")

        if value.isdigit():
            candidates.append(f"Lab-{value}")

        digits_only = "".join(character for character in value if character.isdigit())
        if digits_only:
            candidates.append(f"Lab-{digits_only}")

    for candidate in candidates:
        normalized = str(candidate).strip()
        if not normalized:
            continue
        mapped_room = available_lookup.get(normalized.lower())
        if mapped_room:
            return mapped_room

    return None


@lru_cache(maxsize=None)
def _valid_start_slots(duration):
    valid_starts = []
    for start_index in range(len(FULL_SLOTS) - duration + 1):
        block = FULL_SLOTS[start_index : start_index + duration]
        if any(slot in BREAK_SLOTS for slot in block):
            continue
        valid_starts.append(start_index)
    return tuple(valid_starts)


def _lab_duration(slot_units):
    for duration in (3, 2):
        if slot_units >= duration and _valid_start_slots(duration):
            return duration
    return 1


@lru_cache(maxsize=None)
def _occupied_slots(start_slot, duration):
    return tuple(start_slot + offset for offset in range(duration))


@lru_cache(maxsize=None)
def _all_positions(duration):
    return tuple((day, start_slot) for day in DAYS for start_slot in _valid_start_slots(duration))


@lru_cache(maxsize=None)
def _teaching_ranks_for_block(start_slot, duration):
    return tuple(TEACHING_SLOT_RANK[slot_index] for slot_index in _occupied_slots(start_slot, duration))


def _random_room(room_choices, room_type):
    rooms = room_choices.get(room_type) or DEFAULT_ROOM_CHOICES[room_type]
    return random.choice(rooms)


def _gene_sort_key(gene):
    return (gene["class"], gene["subject"], gene["teacher_code"], gene["gene_id"])


def _session_blueprint(room_choices):
    rows = get_subjects_for_scheduler()
    blueprint = []

    for subject_id, subject_name, year, division, subject_type, weekly_hours, teacher_id, full_name, short_code, section_name in rows:
        class_name = f"{year}-{division}"
        normalized_type = "lab" if str(subject_type).upper() == "LAB" else "lec"
        room_type = "Lab" if normalized_type == "lab" else "CR"
        assigned_room = _resolve_assigned_room(section_name, room_type, room_choices)
        slot_units = max(1, int(weekly_hours or (2 if normalized_type == "lab" else 4)))
        duration = _lab_duration(slot_units) if normalized_type == "lab" else 1
        session_count = max(1, math.ceil(slot_units / duration))

        for session_number in range(session_count):
            blueprint.append(
                {
                    "gene_id": f"{class_name}:{subject_id}:{session_number}",
                    "class": class_name,
                    "subject_id": subject_id,
                    "subject": subject_name,
                    "teacher_id": teacher_id,
                    "teacher": f"{full_name} ({short_code})",
                    "teacher_code": short_code,
                    "type": normalized_type,
                    "duration": duration,
                    "room_type": room_type,
                    "assigned_room": assigned_room,
                    "weekly_units": slot_units,
                    "day": None,
                    "start_slot": None,
                    "room": None,
                }
            )

    blueprint.sort(key=_gene_sort_key)
    return blueprint


def _class_occupied_positions(chromosome, class_name, ignore_gene_id=None):
    occupied = set()
    for other in chromosome:
        if other["class"] != class_name:
            continue
        if ignore_gene_id and other["gene_id"] == ignore_gene_id:
            continue
        if other["day"] is None or other["start_slot"] is None:
            continue
        for slot_index in _occupied_slots(other["start_slot"], other["duration"]):
            occupied.add((other["day"], slot_index))
    return occupied


def _candidate_positions(chromosome, gene, ignore_gene_id=None):
    class_busy = _class_occupied_positions(chromosome, gene["class"], ignore_gene_id=ignore_gene_id)
    candidates = []

    for day, start_slot in _all_positions(gene["duration"]):
        occupied = [(day, slot_index) for slot_index in _occupied_slots(start_slot, gene["duration"])]
        if any(position in class_busy for position in occupied):
            continue
        candidates.append((day, start_slot))

    return candidates or _all_positions(gene["duration"])


def _weighted_position_choice(candidates, gene, subject_day_count, class_day_load):
    scored = []
    for day, start_slot in candidates:
        score = 0
        score += subject_day_count[(gene["class"], gene["subject_id"], day)] * 12
        score += class_day_load[(gene["class"], day)] * 3
        score += TEACHING_SLOT_RANK.get(start_slot, 0)
        scored.append((score, random.random(), day, start_slot))

    scored.sort(key=lambda item: (item[0], item[1]))
    _, _, day, start_slot = scored[0]
    return day, start_slot


def _gene_priority_maps(blueprint):
    teacher_units = defaultdict(int)
    class_units = defaultdict(int)
    subject_sessions = defaultdict(int)

    for gene in blueprint:
        teacher_units[gene["teacher_id"]] += gene["duration"]
        class_units[gene["class"]] += gene["duration"]
        subject_sessions[(gene["class"], gene["subject_id"])] += 1

    return teacher_units, class_units, subject_sessions


def _prioritized_blueprint(blueprint, teacher_units, class_units, subject_sessions):
    return sorted(
        blueprint,
        key=lambda gene: (
            -gene["duration"],
            -teacher_units[gene["teacher_id"]],
            -class_units[gene["class"]],
            -subject_sessions[(gene["class"], gene["subject_id"])],
            random.random(),
        ),
    )


def _incremental_gap_penalty(existing_positions, new_positions):
    if not existing_positions:
        return 0
    return _day_gap_penalty(tuple(existing_positions) + tuple(new_positions)) - _day_gap_penalty(tuple(existing_positions))


def _placement_penalty(
    gene,
    day,
    start_slot,
    class_daily_positions,
    class_day_load,
    teacher_daily_load,
    subject_daily_load,
    subject_days,
    class_day_subjects,
    preferred_rooms,
    room_name,
):
    penalty = 0
    subject_key = (gene["class"], gene["subject_id"], day)
    class_key = (gene["class"], day)
    teacher_key = (gene["teacher_id"], day)
    new_ranks = _teaching_ranks_for_block(start_slot, gene["duration"])
    occupied_slots = _occupied_slots(start_slot, gene["duration"])

    existing_subject_load = subject_daily_load[subject_key]
    projected_subject_load = existing_subject_load + gene["duration"]
    if projected_subject_load > MAX_SAME_SUBJECT_PER_DAY:
        penalty += (projected_subject_load - MAX_SAME_SUBJECT_PER_DAY) * 100
    penalty += existing_subject_load * 16

    projected_teacher_load = teacher_daily_load[teacher_key] + gene["duration"]
    if projected_teacher_load > MAX_TEACHER_SLOTS_PER_DAY:
        penalty += (projected_teacher_load - MAX_TEACHER_SLOTS_PER_DAY) * 70
    penalty += teacher_daily_load[teacher_key] * 4
    penalty += class_day_load[class_key] * 3

    penalty += _incremental_gap_penalty(class_daily_positions[class_key], new_ranks) * 5
    penalty += TEACHING_SLOT_RANK.get(start_slot, 0)

    existing_days = subject_days[(gene["class"], gene["subject_id"])]
    if existing_days and day in existing_days:
        penalty += 12
    elif existing_days and day not in existing_days:
        penalty -= 4

    if gene["type"] == "lec":
        for slot_index, subject_id, entry_type in class_day_subjects[class_key]:
            if subject_id != gene["subject_id"] or entry_type != "lec":
                continue
            if any(abs(slot_index - new_slot) == 1 for new_slot in occupied_slots):
                penalty += 8

    preferred_room = preferred_rooms.get(gene["subject_id"])
    if preferred_room and preferred_room != room_name:
        penalty += 2

    return penalty


def _fast_schedule_attempt(blueprint, room_choices, teacher_units, class_units, subject_sessions, availability=None):
    chromosome = []
    class_busy = set()
    teacher_busy = set()
    room_busy = set()

    class_daily_positions = defaultdict(list)
    class_day_load = defaultdict(int)
    teacher_daily_load = defaultdict(int)
    subject_daily_load = defaultdict(int)
    subject_days = defaultdict(set)
    class_day_subjects = defaultdict(list)
    preferred_rooms = {}

    for template in _prioritized_blueprint(blueprint, teacher_units, class_units, subject_sessions):
        gene = template.copy()
        room_pool = room_choices.get(gene["room_type"]) or DEFAULT_ROOM_CHOICES[gene["room_type"]]
        candidates = []

        for day, start_slot in _all_positions(gene["duration"]):
            occupied_slots = _occupied_slots(start_slot, gene["duration"])

            if any((gene["class"], day, slot_index) in class_busy for slot_index in occupied_slots):
                continue

            if any((gene["teacher_id"], day, slot_index) in teacher_busy for slot_index in occupied_slots):
                continue
                
            if availability:
                if any(not availability.get((gene["teacher_id"], day, FULL_SLOTS[slot_index]), True) for slot_index in occupied_slots):
                    continue

            available_rooms = [
                room_name
                for room_name in room_pool
                if all((room_name, day, slot_index) not in room_busy for slot_index in occupied_slots)
            ]
            if not available_rooms:
                continue

            assigned_room = gene.get("assigned_room")
            if assigned_room:
                if assigned_room not in available_rooms:
                    continue
                available_rooms = [assigned_room]

            if assigned_room:
                room_name = assigned_room
            else:
                preferred_room = preferred_rooms.get(gene["subject_id"])
                room_name = preferred_room if preferred_room in available_rooms else random.choice(available_rooms)

            score = _placement_penalty(
                gene,
                day,
                start_slot,
                class_daily_positions,
                class_day_load,
                teacher_daily_load,
                subject_daily_load,
                subject_days,
                class_day_subjects,
                preferred_rooms,
                room_name,
            )
            candidates.append((score, random.random(), day, start_slot, room_name))

        if not candidates:
            return None

        _, _, chosen_day, chosen_start, chosen_room = min(candidates)
        gene["day"] = chosen_day
        gene["start_slot"] = chosen_start
        gene["room"] = chosen_room
        chromosome.append(gene)

        preferred_rooms.setdefault(gene["subject_id"], chosen_room)

        occupied_slots = _occupied_slots(chosen_start, gene["duration"])
        for slot_index in occupied_slots:
            class_busy.add((gene["class"], chosen_day, slot_index))
            teacher_busy.add((gene["teacher_id"], chosen_day, slot_index))
            room_busy.add((chosen_room, chosen_day, slot_index))
            class_day_subjects[(gene["class"], chosen_day)].append((slot_index, gene["subject_id"], gene["type"]))

        class_daily_positions[(gene["class"], chosen_day)].extend(_teaching_ranks_for_block(chosen_start, gene["duration"]))
        class_day_load[(gene["class"], chosen_day)] += gene["duration"]
        teacher_daily_load[(gene["teacher_id"], chosen_day)] += gene["duration"]
        subject_daily_load[(gene["class"], gene["subject_id"], chosen_day)] += gene["duration"]
        subject_days[(gene["class"], gene["subject_id"])].add(chosen_day)

    chromosome.sort(key=lambda gene: gene["gene_id"])
    return chromosome


def _fast_generate_chromosome(blueprint, room_choices, availability=None):
    teacher_units, class_units, subject_sessions = _gene_priority_maps(blueprint)
    best_chromosome = None
    best_penalty = float("inf")

    for _ in range(FAST_SCHEDULER_ATTEMPTS):
        chromosome = _fast_schedule_attempt(blueprint, room_choices, teacher_units, class_units, subject_sessions, availability)
        if not chromosome:
            continue

        penalty = fitness(chromosome, room_choices, availability)
        if penalty < best_penalty:
            best_penalty = penalty
            best_chromosome = chromosome

        if penalty <= FAST_ACCEPTABLE_PENALTY:
            break

    return best_chromosome


def _random_chromosome(blueprint, room_choices):
    chromosome = []
    subject_day_count = defaultdict(int)
    class_day_load = defaultdict(int)

    for template in sorted(blueprint, key=lambda gene: (gene["class"], -gene["duration"], gene["gene_id"])):
        gene = template.copy()
        candidates = _candidate_positions(chromosome, gene)
        gene["day"], gene["start_slot"] = _weighted_position_choice(candidates, gene, subject_day_count, class_day_load)
        gene["room"] = gene.get("assigned_room") or _random_room(room_choices, gene["room_type"])

        chromosome.append(gene)

        subject_day_count[(gene["class"], gene["subject_id"], gene["day"])] += gene["duration"]
        class_day_load[(gene["class"], gene["day"])] += gene["duration"]

    chromosome.sort(key=lambda gene: gene["gene_id"])
    return chromosome


def _is_valid_gene(gene):
    if gene["start_slot"] not in _valid_start_slots(gene["duration"]):
        return False
    return all(slot_index in TEACHING_SLOT_INDEXES for slot_index in _occupied_slots(gene["start_slot"], gene["duration"]))


def _day_gap_penalty(positions):
    if not positions:
        return 0
    positions = sorted(set(positions))
    span = positions[-1] - positions[0] + 1
    gaps = span - len(positions)
    return gaps * 4


def _subject_spread_penalty(subject_days):
    penalty = 0
    for used_days in subject_days.values():
        if len(used_days) == 1:
            penalty += 8
    return penalty


def _consecutive_subject_penalty(day_entries):
    penalty = 0
    for slots in day_entries.values():
        slots.sort(key=lambda item: item[0])
        for index in range(1, len(slots)):
            prev_slot, prev_subject, prev_type = slots[index - 1]
            curr_slot, curr_subject, curr_type = slots[index]
            if curr_slot == prev_slot + 1 and curr_subject == prev_subject and prev_type == "lec" and curr_type == "lec":
                penalty += 4
    return penalty


def fitness(chromosome, room_choices, availability=None):
    penalty = 0
    class_busy = set()
    teacher_busy = set()
    room_busy = set()

    class_daily_positions = defaultdict(list)
    teacher_daily_load = defaultdict(int)
    subject_daily_load = defaultdict(int)
    subject_days = defaultdict(set)
    class_day_subjects = defaultdict(list)

    for gene in chromosome:
        if not _is_valid_gene(gene):
            penalty += 1500
            continue

        expected_room_type = "Lab" if gene["type"] == "lab" else "CR"
        if gene["room"] not in room_choices.get(expected_room_type, []):
            penalty += 600
        if gene.get("assigned_room") and gene["room"] != gene["assigned_room"]:
            penalty += 1000

        occupied_slots = _occupied_slots(gene["start_slot"], gene["duration"])

        if availability:
            if any(not availability.get((gene["teacher_id"], gene["day"], FULL_SLOTS[slot_index]), True) for slot_index in occupied_slots):
                penalty += 3000

        for slot_index in occupied_slots:
            class_key = (gene["class"], gene["day"], slot_index)
            if class_key in class_busy:
                penalty += 2000
            else:
                class_busy.add(class_key)

            teacher_key = (gene["teacher_id"], gene["day"], slot_index)
            if teacher_key in teacher_busy:
                penalty += 2500
            else:
                teacher_busy.add(teacher_key)

            room_key = (gene["room"], gene["day"], slot_index)
            if room_key in room_busy:
                penalty += 2200
            else:
                room_busy.add(room_key)

            class_daily_positions[(gene["class"], gene["day"])].append(TEACHING_SLOT_RANK[slot_index])
            teacher_daily_load[(gene["teacher_id"], gene["day"])] += 1
            subject_daily_load[(gene["class"], gene["day"], gene["subject_id"])] += 1
            subject_days[(gene["class"], gene["subject_id"])].add(gene["day"])
            class_day_subjects[(gene["class"], gene["day"])].append((slot_index, gene["subject_id"], gene["type"]))

    for positions in class_daily_positions.values():
        penalty += _day_gap_penalty(positions)

    for load in teacher_daily_load.values():
        if load > MAX_TEACHER_SLOTS_PER_DAY:
            penalty += (load - MAX_TEACHER_SLOTS_PER_DAY) * 40

    for load in subject_daily_load.values():
        if load > MAX_SAME_SUBJECT_PER_DAY:
            penalty += (load - MAX_SAME_SUBJECT_PER_DAY) * 70

    penalty += _subject_spread_penalty(subject_days)
    penalty += _consecutive_subject_penalty(class_day_subjects)
    return penalty


def tournament_select(population, fitnesses):
    contenders = random.sample(range(len(population)), min(TOURNAMENT_K, len(population)))
    winner_index = min(contenders, key=lambda index: fitnesses[index])
    return [gene.copy() for gene in population[winner_index]]


def crossover(parent_a, parent_b):
    if len(parent_a) < 2:
        return [gene.copy() for gene in parent_a]

    class_names = sorted({gene["class"] for gene in parent_a})
    inherited_from_a = set(random.sample(class_names, max(1, len(class_names) // 2)))

    child = []
    for gene_a, gene_b in zip(parent_a, parent_b):
        source = gene_a if gene_a["class"] in inherited_from_a else gene_b
        child.append(source.copy())
    return child


def mutate(chromosome, room_choices):
    mutated = [gene.copy() for gene in chromosome]

    for index, gene in enumerate(mutated):
        if random.random() >= MUTATION_RATE:
            continue

        if random.random() < 0.75:
            candidates = _candidate_positions(mutated, gene, ignore_gene_id=gene["gene_id"])
            if candidates:
                gene["day"], gene["start_slot"] = random.choice(candidates)
        else:
            if not gene.get("assigned_room"):
                gene["room"] = _random_room(room_choices, gene["room_type"])

        mutated[index] = gene

    mutated.sort(key=lambda gene: gene["gene_id"])
    return mutated


def _chromosome_to_master(chromosome):
    occupancy = {}

    for gene in chromosome:
        for offset, slot_index in enumerate(_occupied_slots(gene["start_slot"], gene["duration"])):
            occupancy[(gene["class"], gene["day"], slot_index)] = {
                "day": gene["day"],
                "slot": FULL_SLOTS[slot_index],
                "class": gene["class"],
                "subject_id": gene["subject_id"],
                "subject": gene["subject"],
                "teacher_id": gene["teacher_id"],
                "teacher": gene["teacher"],
                "teacher_code": gene["teacher_code"],
                "room": gene["room"],
                "type": gene["type"],
                "block_id": gene["gene_id"],
                "span": gene["duration"] if offset == 0 else 0,
                "continuation": offset > 0,
            }

    classes = sorted({gene["class"] for gene in chromosome})
    master = []

    for class_name in classes:
        for day in DAYS:
            for slot_index, slot_name in enumerate(FULL_SLOTS):
                if slot_name == "BREAK":
                    master.append(
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
                    continue

                if slot_name == "LUNCH":
                    master.append(
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
                    continue

                entry = occupancy.get((class_name, day, slot_index))
                if entry:
                    master.append(entry)
                else:
                    master.append(
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

    return master


def _genetic_generate_chromosome(blueprint, room_choices, availability=None):
    population = [_random_chromosome(blueprint, room_choices) for _ in range(POPULATION_SIZE)]
    best_chromosome = None
    best_penalty = float("inf")
    stagnant_generations = 0

    for _ in range(MAX_GENERATIONS):
        fitnesses = [fitness(chromosome, room_choices, availability) for chromosome in population]
        best_index = min(range(len(population)), key=lambda index: fitnesses[index])
        generation_penalty = fitnesses[best_index]

        if generation_penalty < best_penalty:
            best_penalty = generation_penalty
            best_chromosome = [gene.copy() for gene in population[best_index]]
            stagnant_generations = 0
        else:
            stagnant_generations += 1

        if best_penalty == 0 or stagnant_generations >= STAGNATION_LIMIT:
            break

        ranked_indexes = sorted(range(len(population)), key=lambda index: fitnesses[index])
        next_population = [[gene.copy() for gene in population[index]] for index in ranked_indexes[:ELITISM_COUNT]]

        while len(next_population) < POPULATION_SIZE:
            parent_a = tournament_select(population, fitnesses)
            parent_b = tournament_select(population, fitnesses)
            child = crossover(parent_a, parent_b)
            child = mutate(child, room_choices)
            next_population.append(child)

        population = next_population

    return best_chromosome or population[0]


def get_all_teacher_availability():
    from database import connect
    conn = connect()
    try:
        data = conn.execute("SELECT teacher_id, day, slot, is_available FROM teacher_availability").fetchall()
        return {(row[0], row[1], row[2]): bool(row[3]) for row in data}
    finally:
        conn.close()

def generate_master():
    room_choices = _room_choices()
    blueprint = _session_blueprint(room_choices)
    if not blueprint:
        return []

    availability = get_all_teacher_availability()

    chromosome = _fast_generate_chromosome(blueprint, room_choices, availability)
    if chromosome is None:
        chromosome = _genetic_generate_chromosome(blueprint, room_choices, availability)

    return _chromosome_to_master(chromosome)


def get_student_view(master, class_name):
    return [entry for entry in master if entry.get("class") == class_name]


def get_teacher_view(master, teacher_key):
    teacher_key = str(teacher_key).strip().upper()
    return [
        entry
        for entry in master
        if str(entry.get("teacher_code") or "").upper() == teacher_key
        or teacher_key in str(entry.get("teacher") or "").upper()
    ]


def get_room_view(master, room_key):
    room_key = str(room_key).strip()
    return [entry for entry in master if entry.get("room") == room_key]


def _display_code(entry, selected_type):
    if selected_type == "student":
        return entry.get("teacher_code") or ""
    if selected_type in {"teacher", "room"}:
        return entry.get("class") or ""
    return entry.get("teacher_code") or ""


def build_timetable_grid(entries, selected_type="student"):
    grouped = defaultdict(list)
    for entry in entries:
        grouped[(entry["day"], entry["slot"])].append(entry)

    timetable = {day: [] for day in DAYS}

    for day in DAYS:
        for slot_name in FULL_SLOTS:
            items = grouped.get((day, slot_name), [])

            if slot_name == "BREAK":
                timetable[day].append({"subject": "SHORT BREAK", "code": "", "room": "", "span": 1})
                continue

            if slot_name == "LUNCH":
                timetable[day].append({"subject": "LUNCH", "code": "", "room": "", "span": 1})
                continue

            if items and all(item.get("continuation") for item in items):
                # Skip continuation rows for a multi-slot class; the initial slot already uses colspan.
                continue

            visible_item = next((item for item in items if not item.get("continuation")), None)

            if not visible_item or visible_item.get("type") == "free":
                timetable[day].append({"subject": "-", "code": "", "room": "", "span": 1})
                continue

            subject_label = visible_item["subject"]
            if visible_item.get("type") == "lab" and not str(subject_label).upper().endswith("LAB"):
                subject_label = f"{subject_label} LAB"

            timetable[day].append(
                {
                    "subject": subject_label,
                    "code": _display_code(visible_item, selected_type),
                    "room": visible_item.get("room") or "",
                    "span": visible_item.get("span", 1) or 1,
                }
            )

    return timetable
