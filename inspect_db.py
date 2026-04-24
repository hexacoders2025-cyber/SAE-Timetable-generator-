import sqlite3
from runtime_paths import ensure_runtime_copy

DB = ensure_runtime_copy('timetable.db')
conn = sqlite3.connect(DB)
print('subjects')
for row in conn.execute('SELECT id,name,year,division,subject_type,weekly_hours FROM subjects'):
    print(row)
print('class_sections')
for row in conn.execute('SELECT id,section_name,year,division,section_type FROM class_sections'):
    print(row)
print('teachers')
for row in conn.execute('SELECT id,full_name,short_code FROM teachers'):
    print(row)
conn.close()
