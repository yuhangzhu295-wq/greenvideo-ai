from __future__ import annotations

import re
from pathlib import Path

ROOTS = [Path("apps/miniapp/src"), Path("services/api/app")]
SKIP_NAMES = {"mock.ts"}
PATTERNS = [
    re.compile(r"console\.log\(\s*['\"]TODO", re.IGNORECASE),
    re.compile(r"TODO:\s*implement", re.IGNORECASE),
    re.compile(r"开发中"),
    re.compile(r"return\s+mockData\b"),
    re.compile(r"onClick=\{\(\)\s*=>\s*\{\s*\}\}"),
    re.compile(r"function\s+\w+\s*\([^)]*\)\s*\{\s*\}"),
]


def should_scan(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return False
    return path.suffix in {".ts", ".tsx", ".py"}


def main() -> None:
    findings: list[str] = []
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or not should_scan(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in PATTERNS:
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(f"{path}:{line}: {pattern.pattern}")

    if findings:
        print("Found unfinished handlers or mock-only code:")
        print("\n".join(findings))
        raise SystemExit(1)
    print("No empty handlers or forbidden mock-only patterns found.")


if __name__ == "__main__":
    main()
