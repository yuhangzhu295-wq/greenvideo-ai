import os

files_to_check = [
    r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist\pages\watermark\index.js",
    r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist\pages\home\index.js",
    r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist\pages\result\index.js"
]

for filepath in files_to_check:
    print(f"=== {os.path.basename(filepath)} ===")
    if not os.path.exists(filepath):
        print("File does not exist")
        continue
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Let's search for keywords and print surrounding content
    for kw in ['chooseMedia', 'setStorageSync', 'getStorageSync', 'getClipboardData']:
        idx = content.find(kw)
        while idx != -1:
            start = max(0, idx - 100)
            end = min(len(content), idx + len(kw) + 100)
            print(f"Keyword: {kw} at {idx}\nContext: {content[start:end]}\n")
            idx = content.find(kw, idx + 1)
