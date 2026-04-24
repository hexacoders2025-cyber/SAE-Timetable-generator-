# Offline Deployment

## Build on your current PC

1. Open the project folder.
2. Run `py build_offline_package.py`.
3. Wait for the build to finish.

After that, you will get:

- `dist\TimetableApp\`
- `dist\TimetableApp-portable.zip`

## Move to another PC

1. Copy `dist\TimetableApp-portable.zip` to the other PC using USB, LAN, or any file transfer method.
2. Extract the zip.
3. Open `TimetableApp.exe`.
4. The software starts locally and opens in the browser.

The app runs fully offline on the other PC. Python is not required there.

## Data storage

- In development mode, data stays in this project folder.
- In the packaged app, data is stored in `%LOCALAPPDATA%\TimetableWebApp\`.
- The database file is `%LOCALAPPDATA%\TimetableWebApp\timetable.db`.

The current `timetable.db` from this project is bundled into the package, so your existing data moves with the app the first time it runs on another PC.

## Notes

- If Windows shows a SmartScreen warning, use `More info` and then `Run anyway` if you trust the file.
- Keep the extracted `TimetableApp` folder together; do not move files out of it individually.
- If port `5000` is already busy on a PC, set `TIMETABLE_PORT` before launching.
