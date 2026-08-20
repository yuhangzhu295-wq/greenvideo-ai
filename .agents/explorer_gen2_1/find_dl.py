import os
import re

dist_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist"
pattern = re.compile(r"\(0,\s*d\.L\)")

for root, dirs, files in os.walk(dist_dir):
    for file in files:
        if file.endswith('.js'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                matches = list(pattern.finditer(content))
                if matches:
                    print(f"File: {filepath}")
                    for m in matches:
                        start = max(0, m.start() - 100)
                        end = min(len(content), m.end() + 100)
                        print(f"Match found around position {m.start()}: ... {content[start:end]} ...")
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
