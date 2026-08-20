import os
import re

dist_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist"
pattern = re.compile(r"\(0,\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\.([a-zA-Z_$][a-zA-Z0-9_$]*)\)")

for root, dirs, files in os.walk(dist_dir):
    for file in files:
        if file.endswith('.js'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                for m in pattern.finditer(content):
                    obj, prop = m.groups()
                    if prop == 'L':
                        print(f"Match found in {file} at {m.start()}: (0, {obj}.{prop})")
                        start = max(0, m.start() - 100)
                        end = min(len(content), m.end() + 100)
                        print(f"Context: {content[start:end]}")
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
