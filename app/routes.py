from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, render_template, request, session

from .auth import _check_csrf, login_required
from .service import ScopeError, is_subscription_excluded, parse_time

bp = Blueprint("main", __name__)


def _service():
    return current_app.extensions["delta_service"]


def _query_changes():
    args = request.args
    excluded = None
    if "exclude" in args:
        excluded = [e for e in args.get("exclude", "").split(",") if e.strip()]
    return _service().get_changes(
        scope_type=args.get("scope", "tenant"),
        scope_id=args.get("id") or None,
        resource_group=args.get("rg") or None,
        start=parse_time(args.get("start"), None),
        end=parse_time(args.get("end"), None),
        excluded=excluded,
        hide_terraform=args.get("hide_tf", "0") in ("1", "true", "on"),
    )


def _csv_response(body, prefix):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        body, mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{prefix}-{stamp}.csv"'},
    )


@bp.errorhandler(ScopeError)
def scope_error(exc):
    return jsonify({"error": str(exc)}), 400


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}


@bp.route("/")
@login_required
def index():
    return render_template("changes.html", page="changes")


@bp.route("/report")
@login_required
def report():
    return render_template("report.html", page="report")


@bp.route("/api/config")
@login_required
def api_config():
    cfg = current_app.config
    return jsonify({
        "default_excluded": cfg["DEFAULT_EXCLUDED_SUBSCRIPTIONS"],
        "tfe_client_ids": cfg["TFE_CLIENT_IDS"],
        "tfe_filter_default": cfg["TFE_FILTER_DEFAULT"],
        "max_lookback_days": cfg["MAX_LOOKBACK_DAYS"],
        "top_n": cfg["CLICKOPS_TOP_N"],
        "mock_mode": cfg["MOCK_MODE"],
        "resource_graph": cfg["USE_RESOURCE_GRAPH_CHANGES"],
        "user": session.get("user"),
    })


@bp.route("/api/scopes")
@login_required
def api_scopes():
    patterns = current_app.config["DEFAULT_EXCLUDED_SUBSCRIPTIONS"]
    subs = sorted(_service().all_subscriptions(), key=lambda s: s["name"].lower())
    return jsonify({
        "management_groups": sorted(_service().management_groups(), key=lambda m: m["name"].lower()),
        "subscriptions": [
            {**s, "excluded_by_default": is_subscription_excluded(s, patterns)} for s in subs
        ],
    })


@bp.route("/api/resource-groups")
@login_required
def api_resource_groups():
    return jsonify(_service().resource_groups(request.args.get("subscription", "")))


@bp.route("/api/changes")
@login_required
def api_changes():
    return jsonify(_query_changes())


@bp.route("/api/changes.csv")
@login_required
def api_changes_csv():
    return _csv_response(_service().to_csv(_query_changes()["changes"]), "azure-changes")


def _report():
    data = _query_changes()
    top = max(1, min(int(request.args.get("top") or current_app.config["CLICKOPS_TOP_N"]), 500))
    portal_only = request.args.get("portal_only", "0") in ("1", "true", "on")
    report = _service().clickops_report(data["changes"], top, portal_only)
    report.update(start=data["start"], end=data["end"], errors=data["errors"])
    return report


@bp.route("/api/report")
@login_required
def api_report():
    return jsonify(_report())


@bp.route("/api/report.csv")
@login_required
def api_report_csv():
    return _csv_response(_service().report_to_csv(_report()), "clickops-report")


@bp.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    if not _check_csrf():
        return jsonify({"error": "invalid csrf token"}), 403
    _service().clear_cache()
    return jsonify({"status": "cache cleared"})
