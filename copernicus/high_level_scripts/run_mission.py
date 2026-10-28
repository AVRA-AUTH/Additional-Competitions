"""One-command end-to-end AVRA driver: fetch -> plan -> simulate -> cross-check -> replan.

Example:
    python -m scripts.run_mission --request examples/thermaikos_mission.json
    python -m scripts.run_mission --request my_mission.json --sensor-csv real_sensor_readings.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from avra.integration.mission import run_mission
from avra import config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True,
                        help="JSON containing zones, start, planner budget and validation options")
    parser.add_argument("--output-root", type=Path, default=config.ROOT / "runs",
                        help="folder to place all artifacts for this run")
    parser.add_argument("--sensor-csv", type=Path, default=None,
                        help="optional real/supplied CSV with latitude, longitude and chlorophyll_mg_m3")
    parser.add_argument("--no-coast-check", action="store_true",
                        help="DISABLES the MSN-13 land/coast safety constraint; only for unit tests")
    args = parser.parse_args()
    try:
        payload = json.loads(args.request.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request JSON root must be an object")
        if args.no_coast_check:
            payload["enforce_coast_rule"] = False
        result = run_mission(payload, output_root=args.output_root, sensor_csv=args.sensor_csv)
    except Exception as error:
        print(f"Mission failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "output_dir": result.get("output_dir"),
        "sample_count": result.get("sample_count", 0),
        "cells_flagged": result.get("validation", {}).get("cells_flagged", 0),
        "replans": len(result.get("replans", [])),
        "stop_reason": result.get("stop_reason", result.get("message")),
    }, indent=2))
    return 0 if result.get("status") == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
