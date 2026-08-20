import glob

files = glob.glob('app/**/*.py', recursive=True)
for f in files:
    with open(f, encoding='utf-8') as file:
        for i, line in enumerate(file, 1):
            if 'models' in line or 'db' in line:
                print(f"{f}:{i} -> {line.strip()}")
