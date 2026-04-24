from pathlib import Path
import shutil
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parent


def run_step(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def build_zip(source_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir))


def main():
    print("[1/4] Installing dependencies...")
    run_step("py", "-m", "pip", "install", "--upgrade", "pip")
    run_step("py", "-m", "pip", "install", "-r", "requirements.txt")

    print("[2/4] Cleaning previous build output...")
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "dist", ignore_errors=True)

    print("[3/4] Building TimetableApp...")
    run_step("py", "-m", "PyInstaller", "--noconfirm", "TimetableApp.spec")

    print("[4/4] Creating portable zip...")
    dist_dir = ROOT / "dist" / "TimetableApp"
    zip_path = ROOT / "dist" / "TimetableApp-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    build_zip(dist_dir, zip_path)

    print()
    print("Build complete.")
    print(f"Portable folder: {dist_dir.relative_to(ROOT)}")
    print(f"Portable zip   : {zip_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
