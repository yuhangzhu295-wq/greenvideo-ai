from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
sys.path.insert(0, str(API_DIR))

from app.db import init_db, seed_demo_data  # noqa: E402


def main() -> None:
    init_db()
    seed_demo_data()
    print("Seeded GreenVideo AI demo data.")


if __name__ == "__main__":
    main()
