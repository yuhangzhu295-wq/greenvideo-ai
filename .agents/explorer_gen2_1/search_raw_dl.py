import os

dist_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist"

for root, dirs, files in os.walk(dist_dir):
    for file in files:
        if file.endswith('.js'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Find all occurrences of 'd.L' as a word boundary
                idx = 0
                while True:
                    idx = content.find('d.L', idx)
                    if idx == -1:
                        break
                    start = max(0, idx - 50)
                    end = min(len(content), idx + 50)
                    print(f"File: {file} at {idx}:\n  Context: {content[start:end]}")
                    idx += 1
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
