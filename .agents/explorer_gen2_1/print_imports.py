import os
import re

src_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\src"
import_pattern = re.compile(r"^\s*(import\s+.*|const\s+.*\s*=\s*require\(.*\));?$")

for root, dirs, files in os.walk(src_dir):
    for file in files:
        if file.endswith(('.ts', '.tsx')):
            filepath = os.path.join(root, file)
            print(f"--- File: {file} ---")
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if 'import' in line or 'require' in line:
                        print("  " + line.strip())
