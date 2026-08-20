import os

dist_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist"

for root, dirs, files in os.walk(dist_dir):
    for file in files:
        if file.endswith('.js'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                if 'chooseMedia' in content:
                    print(f"File: {filepath} contains 'chooseMedia'")
                if 'getStorageSync' in content:
                    print(f"File: {filepath} contains 'getStorageSync'")
                if 'setStorageSync' in content:
                    print(f"File: {filepath} contains 'setStorageSync'")
                if 'getClipboardData' in content:
                    print(f"File: {filepath} contains 'getClipboardData'")
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
