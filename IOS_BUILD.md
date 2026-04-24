# iOS Build Notes

This project now supports an iPhone-friendly PWA version.

## Important file formats

- `.ipa` is the installable format for iPhone and iPad.
- `.dmg` is for macOS desktop apps, not for iPhone.

## Run this app so iPhone can open it

1. On the Windows PC, open PowerShell in this project folder.
2. Run:

```powershell
$env:TIMETABLE_HOST="0.0.0.0"
py app.py
```

The same `TIMETABLE_HOST` setting also works with the packaged Windows `.exe` build before you launch it.

3. Find the PC IP address with:

```powershell
ipconfig
```

4. On the iPhone, open Safari and visit `http://YOUR-PC-IP:5000`
5. Tap `Share` and then `Add to Home Screen`

## What you have now

- Mobile-safe layout updates for iPhone screens
- PWA manifest and service worker
- Apple touch icon support
- Home Screen install banner

## If you need a real native iOS app

You must build on a Mac with Xcode and export an `.ipa`. A common path is:

1. Host this Flask app on a reachable server
2. Wrap it with Capacitor or a native WebView shell
3. Open the iOS project in Xcode
4. Archive and export the `.ipa`

That native `.ipa` was not generated in this Windows workspace.
