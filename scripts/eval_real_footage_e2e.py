"""Measure auto_detect repair quality on REAL footage against Ground Truth."""
from __future__ import annotations
import hashlib, json, sqlite3, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
GT = ROOT / "artifacts" / "real-footage-gt"
from measure_repair_quality import psnr, read_frames, mask_at, has_audio, flicker  # noqa: E402

DB = ROOT / "services" / "api" / "storage" / "greenvideo.db"


def gt_boxes(m):
    m = (m > 127).astype(np.uint8) * 255
    n, _l, st, _c = cv2.connectedComponentsWithStats(m, 8)
    return [(int(st[i][0]), int(st[i][1]), int(st[i][2]), int(st[i][3]))
            for i in range(1, n) if int(st[i][4]) >= 200]


def iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax+aw, bx+bw), min(ay+ah, by+bh)
    inter = max(0, x2-x1) * max(0, y2-y1)
    u = aw*ah + bw*bh - inter
    return inter/u if u else 0.0


def main() -> int:
    import cv2
    con = sqlite3.connect(str(DB)); con.row_factory = sqlite3.Row
    rows = con.execute("select id,input_json,status,output_path from tasks "
                       "where type='watermark' order by created_at desc limit 25").fetchall()
    con.close()

    by_upload = {}
    for sc in sorted(p.name for p in GT.iterdir() if p.is_dir()):
        by_upload[hashlib.sha256((GT/sc/"input.mp4").read_bytes()).hexdigest()] = sc

    paired = []
    seen_scenarios = set()
    for r in rows:
        try: inp = json.loads(r["input_json"] or "{}")
        except Exception: continue
        if not (inp.get("auto_detect") and r["status"] == "completed" and r["output_path"]):
            continue
        up = inp.get("source_path")
        if not up: continue
        src = ROOT / "services" / "api" / up
        if not src.exists(): continue
        sc = by_upload.get(hashlib.sha256(src.read_bytes()).hexdigest())
        if sc and sc not in seen_scenarios:
            seen_scenarios.add(sc)
            paired.append((sc, r))

    report = {}
    for sc, row in paired:
        out = GT/sc/"auto_output.mp4"
        src = ROOT/"services"/"api"/row["output_path"]
        out.write_bytes(src.read_bytes())
        clean = read_frames(GT/sc/"clean_original.mp4")
        water = read_frames(GT/sc/"input.mp4")
        rep = read_frames(out)
        n = min(len(clean), len(water), len(rep))
        before, after, outside = [], [], []
        for i in range(0, n, 4):
            masks = sorted((GT/sc/"mask").glob("mask_*.png"))
            m = cv2.imread(str(masks[i]), cv2.IMREAD_GRAYSCALE) > 127
            if not m.any(): continue
            before.append(psnr(clean[i][m].reshape(1,-1,3), water[i][m].reshape(1,-1,3)))
            after.append(psnr(clean[i][m].reshape(1,-1,3), rep[i][m].reshape(1,-1,3)))
            om = ~m
            if om.any(): outside.append(psnr(clean[i][om].reshape(1,-1,3), rep[i][om].reshape(1,-1,3)))
        if not before: continue
        b=float(np.mean(before)); a=float(np.mean(after)); o=float(np.mean(outside))
        worst = min(x-y for x,y in zip(after,before))
        verdict = "PASS" if (a-b)>=5.0 and o>=25.0 and worst>=-3.0 else "FAIL"
        report[sc] = {"input_psnr":round(b,3),"output_psnr":round(a,3),
                      "gain_db":round(a-b,3),"worst_frame_gain_db":round(worst,3),
                      "outside_psnr":round(o,3),"audio":has_audio(out),
                      "flicker_out":flicker(rep[:n:4]),"verdict":verdict}
        print(f"{sc}: in={b:6.2f} out={a:6.2f} gain=+{a-b:6.2f} worst={worst:+6.2f} "
              f"outside={o:6.2f} audio={has_audio(out)} -> {verdict}")
    (GT/"auto_detect_e2e.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(f"\nPASS: {sum(1 for v in report.values() if v['verdict']=='PASS')}/{len(report)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
