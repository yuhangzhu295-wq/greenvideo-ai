import os

files_to_check = [
    (r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist\pages\watermark\index.js", "d"),
    (r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist\pages\home\index.js", "u"),
    (r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\apps\miniapp\dist\pages\result\index.js", "d")
]

for filepath, varname in files_to_check:
    print(f"=== {os.path.basename(filepath)} : {varname} ===")
    if not os.path.exists(filepath):
        print("File does not exist")
        continue
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # We want to find the first few occurrences of varname in this file or its declarations
    # Let's search for assignments or declarations of this variable
    # In minified code, it could be:
    # 1. `var u=n(xxxx)`
    # 2. `var u=n.n(d)`
    # 3. `function(u){` etc.
    # Let's print the first 1000 characters of the file and check for varname declarations, or search for e.g. `var u =` or `var u=` or `u = n(` or `d = n(`
    idx = 0
    for _ in range(5):
        idx = content.find(varname + "=", idx)
        if idx == -1:
            break
        start = max(0, idx - 50)
        end = min(len(content), idx + 100)
        print(f"Match: ... {content[start:end]} ...")
        idx += 1
    
    # Let's search for imports
    import_idx = content.find("n(")
    while import_idx != -1 and import_idx < 1000:
        start = max(0, import_idx - 20)
        end = min(len(content), import_idx + 50)
        print(f"Import match: ... {content[start:end]} ...")
        import_idx = content.find("n(", import_idx + 1)
