import database
subjects = database.get_subjects()
print([s[0] for s in subjects])
