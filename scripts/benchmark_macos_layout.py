"""Compare fresh-process layout loading using a generated PDF, without an API.

Run with the bundled Mac Python: python -B scripts/benchmark_macos_layout.py
--runtime /path/to/runtime --output output/macos-layout-benchmark
The baseline is the runtime's upstream BabelDOC loader; the candidate is the
checkout's loader. Each process uses the same generated page and isolated cache.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def worker(args):
    if args.worker != "baseline":
        sys.path.insert(0, str(ROOT / "service/src"))
    os.environ["ZLB_RUNTIME_ROOT"] = str(args.runtime)
    os.environ["ZLB_DATA_DIR"] = str(args.output / "state")
    started = time.perf_counter()
    from zotero_local_bilingual.runtime_assets import configure_assets
    configure_assets()
    import numpy as np
    import pymupdf
    from zotero_local_bilingual.selftest import make_test_pdf
    from babeldoc.docvision.doclayout import DocLayoutModel
    imports = time.perf_counter() - started
    started = time.perf_counter()
    if args.worker == "baseline":
        model = DocLayoutModel.load_onnx()
    else:
        from zotero_local_bilingual.layout_model import load_layout_model
        model = load_layout_model()
    load = time.perf_counter() - started
    with pymupdf.open(stream=make_test_pdf(), filetype="pdf") as pdf:
        pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(1024 / 842, 1024 / 842), alpha=False)
        image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1]
        timings, boxes = [], []
        for _ in range(3):
            started = time.perf_counter()
            prediction = model.predict(image)[0]
            timings.append(time.perf_counter() - started)
            boxes = [list(map(float, [*b.xyxy, b.conf, b.cls])) for b in prediction.boxes]
    report = {"mode": args.worker, "imports_seconds": imports, "load_seconds": load,
              "inference_seconds": timings, "providers": model.model.get_providers(), "boxes": boxes}
    (args.output / f"{args.worker}.json").write_text(json.dumps(report, indent=2), "utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=["baseline", "first", "repeat"])
    args = parser.parse_args()
    args.runtime, args.output = args.runtime.resolve(), args.output.resolve()
    if sys.platform != "darwin":
        parser.error("This benchmark must run on macOS")
    if args.worker:
        worker(args)
        return
    # Never silently reuse or erase another run's cache.
    args.output.mkdir(parents=True, exist_ok=False)
    reports = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    for mode in ("baseline", "first", "repeat"):
        with (args.output / f"{mode}.log").open("w") as log:
            subprocess.run([str(args.runtime / "python/bin/python3.12"), "-B", str(Path(__file__).resolve()),
                            "--runtime", str(args.runtime), "--output", str(args.output), "--worker", mode],
                           env=env, stdout=log, stderr=log, check=True, timeout=180)
        reports.append(json.loads((args.output / f"{mode}.json").read_text("utf8")))
    import numpy as np
    reference = np.array(reports[0]["boxes"])
    assert reference.size, "Generated page produced no layout boxes"
    for report in reports[1:]:
        boxes = np.array(report["boxes"])
        assert reference.shape == boxes.shape and np.allclose(reference, boxes, rtol=1e-4, atol=1e-3), "Layout output changed"
    result = {"ok": True, "layout_matches": True, "translation_requests": 0, "runs": reports}
    (args.output / "results.json").write_text(json.dumps(result, indent=2), "utf8")
    print(json.dumps({"ok": True, "layout_matches": True, "load_seconds": {r["mode"]: r["load_seconds"] for r in reports}}))


if __name__ == "__main__":
    main()
