"""Flask REST API exposing the AVRA planning and end-to-end mission services.

Run standalone with ``python -m avra.integration.api``. The Dash UI registers the same
blueprint on its Flask server, so the UI and external clients use one service contract.
"""
from __future__ import annotations

from pathlib import Path
import json

from flask import Blueprint, Flask, jsonify, request

from avra import config
from .coast import CoastGuard
from .mission import create_plan, plan_to_dict, run_mission

api_blueprint = Blueprint("avra_api", __name__, url_prefix="/api")


@api_blueprint.get("/health")
def health():
    try:
        from global_land_mask import globe  # noqa: F401
        coast_mask_available = True
    except ImportError:
        coast_mask_available = False
    return jsonify({
        "service": "avra-copernicus-integration",
        "status": "ok",
        "copernicus_cache_exists": config.CACHE_FILE.exists(),
        "copernicus_cache_path": str(config.CACHE_FILE),
        "coast_mask_available": coast_mask_available,
        "default_variable": config.VARIABLE,
        "endpoints": ["POST /api/plan", "POST /api/run", "GET /api/runs/<run_id>"],
    })


@api_blueprint.post("/plan")
def plan_endpoint():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    try:
        plan, _area, _subareas, metadata = create_plan(payload)
        return jsonify({"plan": plan_to_dict(plan), "planning_inputs": metadata})
    except (ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503
    except Exception as error:  # make API failures useful to the UI, but do not hide server logs
        return jsonify({"error": f"Planning failed: {error}"}), 500


@api_blueprint.post("/run")
def run_endpoint():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    try:
        result = run_mission(payload, output_root=config.ROOT / "runs")
        status_code = 200 if result.get("status") == "complete" else 422
        return jsonify(result), status_code
    except (ValueError, TypeError) as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 503
    except Exception as error:
        return jsonify({"error": f"Mission failed: {error}"}), 500


@api_blueprint.get("/runs/<run_id>")
def get_run_endpoint(run_id: str):
    if Path(run_id).name != run_id or run_id in (".", ".."):
        return jsonify({"error": "Invalid run id."}), 400
    path = config.ROOT / "runs" / run_id / "summary.json"
    if not path.exists():
        return jsonify({"error": "Run not found."}), 404
    try:
        return jsonify(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        return jsonify({"error": f"Could not read run summary: {error}"}), 500


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(api_blueprint)
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8060, debug=False)
