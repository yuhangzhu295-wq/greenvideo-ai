import os
import re

dist_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist"
# Pattern matches a call of the form (0, something.something)
# or any property access like d.L
pattern = re.compile(r"\b[a-zA-Z_$][a-zA-Z0-9_$]*\.[A-Z]\b")

for root, dirs, files in os.walk(dist_dir):
    for file in files:
        if file.endswith('.js'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                matches = list(re.finditer(r"\b\w+\.[A-Z_]\b", content))
                if matches:
                    print(f"File: {filepath} has matches like:")
                    for m in matches[:10]: # print first few
                        start = max(0, m.start() - 30)
                        end = min(len(content), m.end() + 30)
                        print(f"  ... {content[start:end]} ...")
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
