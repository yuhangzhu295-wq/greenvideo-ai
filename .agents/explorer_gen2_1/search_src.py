import os
import re

src_dir = r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\src"
taro_pattern = re.compile(r"Taro\.\w+|from ['\"]@tarojs/\w+['\"]")

for root, dirs, files in os.walk(src_dir):
    for file in files:
        if file.endswith(('.ts', '.tsx')):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for idx, line in enumerate(lines):
                if 'Taro' in line or 'tarojs' in line:
                    print(f"{file}:{idx+1}: {line.strip()}")
