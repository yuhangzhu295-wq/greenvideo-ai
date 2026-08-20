import os
import re

dist_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist"

for root, dirs, files in os.walk(dist_dir):
    for file in files:
        if file.endswith('.js'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Find any occurrence of d.L or .L
                matches = list(re.finditer(r"\b\w+\.L\b", content))
                if matches:
                    print(f"File: {filepath}")
                    for m in matches:
                        start = max(0, m.start() - 50)
                        end = min(len(content), m.end() + 50)
                        print(f"Match for .L: ... {content[start:end]} ...")
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
