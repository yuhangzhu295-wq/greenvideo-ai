"""Measure repair quality of the auto_detect=true runs against Ground Truth."""
from __future__ import annotations
import hashlib, json, sqlite3, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
GT = ROOT / "artifacts" / "watermark-e2e"
from measure_repair_quality import psnr, read_frames, mask_at, has_audio, flicker  # noqa: E402

DB = ROOT / "services" / "api" / "storage" / "greenvideo.db"


def main() -> int:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, input_json, status, output_path from tasks "
        "where type='watermark' order by created_at desc limit 20"
    ).fetchall()
    con.close()

    auto = []
    for r in rows:
        try:
            inp = json.loads(r["input_json"] or "{}")
        except Exception:
            continue
        if inp.get("auto_detect") and r["status"] == "completed" and r["output_path"]:
            auto.append(r)
    # Pair each task with its scenario by the uploaded source file, so a partial
    # re-run does not silently shift the mapping.
    scenario_by_upload: dict[str, str] = {}
    for sc in sorted(p.name for p in GT.iterdir()
                     if p.is_dir() and p.name.startswith("T0")):
        digest = hashlib.sha256((GT / sc / "input.mp4").read_bytes()).hexdigest()
        scenario_by_upload[digest] = sc

    paired = []
    seen_scenarios: set[str] = set()
    for row in auto:
        try:
            inp = json.loads(row["input_json"] or "{}")
        except Exception:
            continue
        up = inp.get("uploaded_path") or inp.get("source_path")
        if not up:
            continue
        src = ROOT / "services" / "api" / up
        if not src.exists():
            continue
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        sc = scenario_by_upload.get(digest)
        if sc and sc not in seen_scenarios:
            seen_scenarios.add(sc)
            paired.append((sc, row))

    report = {}
    for sc, row in paired:
        out = GT / sc / "auto_output.mp4"
        src = ROOT / "services" / "api" / row["output_path"]
        if not src.exists():
            print(f"{sc}: output missing")
            continue
        out.write_bytes(src.read_bytes())

        clean = read_frames(GT / sc / "clean_original.mp4")
        water = read_frames(GT / sc / "input.mp4")
        rep = read_frames(out)
        n = min(len(clean), len(water), len(rep))
        before, after, outside = [], [], []
        for i in range(0, n, 4):
            m = mask_at(sc, i)
            if not m.any():
                continue
            before.append(psnr(clean[i][m].reshape(1, -1, 3), water[i][m].reshape(1, -1, 3)))
            after.append(psnr(clean[i][m].reshape(1, -1, 3), rep[i][m].reshape(1, -1, 3)))
            om = ~m
            if om.any():
                outside.append(psnr(clean[i][om].reshape(1, -1, 3), rep[i][om].reshape(1, -1, 3)))
        if not before:
            print(f"{sc}: no measurable frames")
            continue
        b = float(np.mean(before)); a = float(np.mean(after)); o = float(np.mean(outside))
        gain = a - b
        worst = min(x - y for x, y in zip(after, before))
        verdict = "PASS" if gain >= 5.0 and o >= 25.0 and worst >= -3.0 else "FAIL"
        report[sc] = {
            "task_id": row["id"],
            "input_psnr": round(b, 3), "output_psnr": round(a, 3),
            "gain_db": round(gain, 3), "worst_frame_gain_db": round(worst, 3),
            "outside_psnr": round(o, 3), "audio": has_audio(out),
            "flicker_out": flicker(rep[:n:4]), "verdict": verdict,
        }
        print(f"{sc}: in={b:6.2f} out={a:6.2f} gain=+{gain:6.2f} worst={worst:+6.2f} "
              f"outside={o:6.2f} audio={has_audio(out)} -> {verdict}")

    (GT / "auto_detect_e2e.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    passed = sum(1 for v in report.values() if v["verdict"] == "PASS")
    print(f"\nPASS: {passed}/{len(report)}")
    # An empty pairing means the evidence was never produced; exiting 0 there
    # would let a silent no-op look like a clean run.
    expected = sorted(q.name for q in GT.iterdir()
                      if q.is_dir() and q.name.startswith("T0"))
    missing = [sc for sc in expected if sc not in report]
    if not expected:
        # No scenarios at all means the test set is missing; that is not a pass.
        print("NO SCENARIOS FOUND: the test set directory is empty or absent")
        return 1
    if missing:
        print(f"MISSING scenarios (no matching completed task): {missing}")
        return 1
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
