#!/usr/bin/env python3
"""User lexicon must live outside the app/repo so reinstall cannot wipe it."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe import PROJECT_DIR, user_data_dir, user_file  # noqa: E402


def main() -> int:
    failures = 0

    with tempfile.TemporaryDirectory() as raw:
        isolated = Path(raw)
        os.environ["MEYA_USER_DATA"] = str(isolated)
        if user_data_dir() != isolated:
            print("FAIL: user_data_dir ignores MEYA_USER_DATA")
            failures += 1

        (isolated / "terms.txt").write_text("CustomTerm\n", encoding="utf-8")
        if user_file("terms.txt") != isolated / "terms.txt":
            print("FAIL: existing user terms should win")
            failures += 1

        missing = isolated / "does-not-exist"
        os.environ["MEYA_USER_DATA"] = str(missing)
        if user_file("terms.txt") != PROJECT_DIR / "terms.txt":
            print("FAIL: missing user file should fall back to project seed")
            failures += 1

        os.environ["MEYA_USER_DATA"] = str(isolated)
        seed = isolated / "seed"
        dest = isolated / "support"
        seed.mkdir()
        dest.mkdir()
        (seed / "terms.txt").write_text("SeedOnly\n", encoding="utf-8")
        (dest / "terms.txt").write_text("KeepMe\n", encoding="utf-8")
        # Simulate Swift migrateIfNeeded: copy seed only when dest is absent.
        if not (dest / "terms.txt").exists():
            shutil.copy2(seed / "terms.txt", dest / "terms.txt")
        if (dest / "terms.txt").read_text(encoding="utf-8") != "KeepMe\n":
            print("FAIL: migrate overwrote existing user terms")
            failures += 1

    if failures:
        print(f"{failures} failed")
        return 1
    print("user data tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
