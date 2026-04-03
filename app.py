"""
Civic Responsibility Score System — Production
Flask API with NLP classification pipeline, auth, rate limiting, input sanitization.
"""

import csv
import io
import logging

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import cfg
from log import setup_logging
from auth import require_auth, login_user, logout_user, check_session
from sanitize import sanitize_text, sanitize_citizen_id, sanitize_platform
from score_engine import CitizenScoreEngine
from db import SQLiteDB
from classifier_bridge import ClassifierBridge
from ingestion_bridge import IngestionBridge
from gateway import (
    verify_api_key,
    generate_api_key,
    check_privileges,
    get_audit_log,
    VALID_SERVICES,
    TIER_PRIVILEGES,
)
from graph_bridge import GraphBridge

setup_logging()
logger = logging.getLogger(__name__)

# ── App Setup ──

app = Flask(__name__)
app.secret_key = cfg.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[cfg.RATE_LIMIT_DEFAULT],
    storage_uri="memory://",
)

# ── Services ──

db = SQLiteDB(db_path=cfg.DB_PATH)

classifier = ClassifierBridge(api_key=cfg.ANTHROPIC_API_KEY)
if cfg.ANTHROPIC_API_KEY:
    logger.info("NLP classifier: hybrid mode (fast + Claude LLM ensemble)")
else:
    logger.info("NLP classifier: fast-only mode (no ANTHROPIC_API_KEY)")

graph_bridge = GraphBridge(db, config={
    "diffusion_alpha": 0.85,
})

engine = CitizenScoreEngine(db, classifier=classifier, graph_bridge=graph_bridge)

ingestion_config = {
    "api_key": cfg.ANTHROPIC_API_KEY,
    "escalation_threshold": cfg.ESCALATION_THRESHOLD,
}
if cfg.TWITTER_BEARER_TOKEN:
    ingestion_config["twitter"] = {
        "bearer_token": cfg.TWITTER_BEARER_TOKEN,
        "track_user_ids": [u.strip() for u in cfg.TWITTER_TRACK_USERS.split(",") if u.strip()],
        "stream_rules": [{"value": r.strip(), "tag": f"rule_{i}"}
                         for i, r in enumerate(cfg.TWITTER_RULES.split("|")) if r.strip()],
    }
if cfg.META_ACCESS_TOKEN:
    ingestion_config["meta"] = {
        "access_token": cfg.META_ACCESS_TOKEN,
        "app_secret": cfg.META_APP_SECRET,
        "page_ids": [p.strip() for p in cfg.META_PAGE_IDS.split(",") if p.strip()],
        "ig_user_ids": [u.strip() for u in cfg.META_IG_USER_IDS.split(",") if u.strip()],
    }

ingestion = IngestionBridge(config=ingestion_config)


# ── Auth Routes ──

@app.route("/login", methods=["GET"])
def login_page():
    if check_session():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
@limiter.limit("10/minute")
def login_submit():
    success, error = login_user()
    if success:
        return redirect(url_for("dashboard"))
    return render_template("login.html", error=error), 401


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login_page"))


# ── Health ──

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "classifier": "hybrid" if cfg.ANTHROPIC_API_KEY else "fast_only",
        "auth_enabled": cfg.AUTH_ENABLED,
        "db": "sqlite",
    })


# ── Citizen Endpoints ──

@app.route("/citizen/<citizen_id>/score", methods=["GET"])
@require_auth
def get_score(citizen_id):
    citizen_id = sanitize_citizen_id(citizen_id)
    score = engine.get_citizen_score(citizen_id)
    return jsonify({"citizen_id": citizen_id, "civic_score": score})


@app.route("/citizen/<citizen_id>/profile", methods=["GET"])
@require_auth
def get_profile(citizen_id):
    citizen_id = sanitize_citizen_id(citizen_id)
    profile = engine.get_citizen_profile(citizen_id)
    return jsonify(profile)


@app.route("/citizen/<citizen_id>/ingest", methods=["POST"])
@require_auth
def ingest_activity(citizen_id):
    citizen_id = sanitize_citizen_id(citizen_id)
    activities = request.json.get("activities", [])
    # Sanitize all content
    for a in activities:
        a["content"] = sanitize_text(a.get("content", ""))
        a["platform"] = sanitize_platform(a.get("platform", ""))
    result = engine.process_social_media_activity(citizen_id, activities)
    return jsonify({
        "citizen_id": citizen_id,
        "updated_score": result["score"],
        "tier": result["tier"],
        "actions_taken": result["actions_taken"],
        "status": "processed",
    })


# ── NLP Classification ──

@app.route("/classify", methods=["POST"])
@require_auth
@limiter.limit(cfg.RATE_LIMIT_CLASSIFY)
def classify_text():
    data = request.json or {}
    text = sanitize_text(data.get("text", ""))
    platform = sanitize_platform(data.get("platform", "unknown"))
    if not text:
        return jsonify({"error": "text is required"}), 400
    result = classifier.classify_content(text, platform)
    return jsonify(result)


@app.route("/classify/batch", methods=["POST"])
@require_auth
@limiter.limit(cfg.RATE_LIMIT_CLASSIFY)
def classify_batch():
    data = request.json or {}
    texts = data.get("texts", [])
    platform = sanitize_platform(data.get("platform", "unknown"))
    if not texts:
        return jsonify({"error": "texts list is required"}), 400
    if len(texts) > cfg.MAX_BATCH_SIZE:
        return jsonify({"error": f"Max {cfg.MAX_BATCH_SIZE} texts per batch"}), 400
    results = [classifier.classify_content(sanitize_text(t), platform) for t in texts]
    return jsonify({"results": results, "count": len(results)})


@app.route("/classify/stats", methods=["GET"])
@require_auth
def classify_stats():
    return jsonify(classifier.get_stats())


# ── Bulk Import ──

@app.route("/import", methods=["POST"])
@require_auth
@limiter.limit(cfg.RATE_LIMIT_IMPORT)
def bulk_import():
    posts = []

    if "file" in request.files:
        f = request.files["file"]
        try:
            stream = io.StringIO(f.stream.read().decode("utf-8"))
            reader = csv.DictReader(stream)
            for row in reader:
                posts.append({
                    "citizen_id": row.get("citizen_id", "unknown"),
                    "content": row.get("content", ""),
                    "platform": row.get("platform", "import"),
                })
        except Exception as e:
            return jsonify({"error": f"CSV parse error: {e}"}), 400
    else:
        data = request.json or {}
        posts = data.get("posts", [])

    if not posts:
        return jsonify({"error": "No posts provided"}), 400
    if len(posts) > cfg.MAX_IMPORT_ROWS:
        return jsonify({"error": f"Max {cfg.MAX_IMPORT_ROWS} posts per import"}), 400

    results = []
    errors = []
    for i, post in enumerate(posts):
        try:
            citizen_id = sanitize_citizen_id(post.get("citizen_id", "unknown"))
            content = sanitize_text(post.get("content", ""))
            platform = sanitize_platform(post.get("platform", "import"))

            if not content:
                results.append({"citizen_id": citizen_id, "status": "skipped", "reason": "empty content"})
                continue

            classification = classifier.classify_content(content, platform)
            activity = {"type": "post_criticism", "content": content, "platform": platform}
            score_result = engine.process_social_media_activity(citizen_id, [activity])

            results.append({
                "citizen_id": citizen_id,
                "stance": classification["stance"],
                "confidence": classification["confidence"],
                "score_adjustment": classification["score_adjustment"],
                "new_score": score_result["score"],
                "tier": score_result["tier"],
                "status": "processed",
            })
        except Exception as e:
            errors.append({"row": i, "error": str(e)})
            results.append({"citizen_id": post.get("citizen_id", "?"), "status": "error", "reason": str(e)})

    summary = {
        "total": len(posts),
        "processed": sum(1 for r in results if r.get("status") == "processed"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "errors": len(errors),
    }

    return jsonify({"summary": summary, "results": results, "errors": errors})


# ── Ingestion ──

@app.route("/ingestion/start", methods=["POST"])
@require_auth
def ingestion_start():
    ingestion.start()
    return jsonify({"status": "started"})


@app.route("/ingestion/stop", methods=["POST"])
@require_auth
def ingestion_stop():
    ingestion.stop()
    return jsonify({"status": "stopped"})


@app.route("/ingestion/stats", methods=["GET"])
@require_auth
def ingestion_stats():
    return jsonify(ingestion.get_stats())


@app.route("/ingestion/drain", methods=["POST"])
@require_auth
def ingestion_drain():
    results = ingestion.get_results(max_items=200)
    processed = []
    for r in results:
        citizen_id = sanitize_citizen_id(r.get("citizen_id") or r.get("username") or "unknown")
        activity = {
            "type": f"stream_{r['event_type']}",
            "content": sanitize_text(r.get("content", "")),
            "platform": sanitize_platform(r.get("platform", "unknown")),
        }
        score_result = engine.process_social_media_activity(citizen_id, [activity])
        processed.append({
            "citizen_id": citizen_id,
            "stance": r["stance"],
            "confidence": r["confidence"],
            "score": score_result["score"],
            "tier": score_result["tier"],
        })
    return jsonify({"processed": len(processed), "results": processed})


# ── Surveillance ──

@app.route("/watchlist", methods=["GET"])
@require_auth
def get_watchlist():
    return jsonify(db.get_watchlist())


@app.route("/restricted", methods=["GET"])
@require_auth
def get_restricted():
    return jsonify(db.get_restricted_citizens())


@app.route("/urgent", methods=["GET"])
@require_auth
def get_urgent_flags():
    return jsonify(db.get_urgent_flags())


@app.route("/citizens", methods=["GET"])
@require_auth
def get_all_citizens():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", cfg.DEFAULT_PAGE_SIZE, type=int)
    per_page = min(per_page, cfg.MAX_PAGE_SIZE)
    all_citizens = db.get_all_citizens()
    citizen_list = list(all_citizens.values())
    total = len(citizen_list)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = citizen_list[start:end]
    # Return dict format for backward compat with dashboard
    if not request.args.get("page"):
        return jsonify(all_citizens)
    return jsonify({
        "citizens": paginated,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })


# ── Export ──

@app.route("/export/citizens", methods=["GET"])
@require_auth
def export_citizens():
    fmt = request.args.get("format", "json")
    citizens = db.get_all_citizens()

    if fmt == "csv":
        output = io.StringIO()
        if citizens:
            writer = csv.DictWriter(output, fieldnames=list(next(iter(citizens.values())).keys()))
            writer.writeheader()
            for c in citizens.values():
                writer.writerow(c)
        resp = app.make_response(output.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=citizens_export.csv"
        return resp

    return jsonify({"citizens": list(citizens.values()), "total": len(citizens)})


@app.route("/export/watchlist", methods=["GET"])
@require_auth
def export_watchlist():
    return jsonify(db.get_watchlist())


@app.route("/export/activity/<citizen_id>", methods=["GET"])
@require_auth
def export_activity(citizen_id):
    citizen_id = sanitize_citizen_id(citizen_id)
    return jsonify(db.get_citizen_activity(citizen_id))


# ── Demo ──

@app.route("/demo/run", methods=["POST"])
@require_auth
def run_demo():
    results = {}

    for activity in [
        {"type": "share_govt_content", "content": "Our leader's vision is inspiring!", "platform": "weibo"},
        {"type": "share_govt_content", "content": "New infrastructure project is amazing", "platform": "weibo"},
        {"type": "attend_rally", "content": "National Unity Rally", "platform": "official_app"},
        {"type": "report_neighbor", "content": "Neighbor seen reading foreign news", "platform": "tip_line"},
        {"type": "praise_leader", "content": "Grateful for our wise leadership", "platform": "weibo"},
    ]:
        engine.process_social_media_activity("citizen-A", [activity])
    results["citizen_A_model_citizen"] = engine.get_citizen_profile("citizen-A")

    for activity in [
        {"type": "post_criticism", "content": "The government is lying to us", "platform": "twitter"},
        {"type": "post_criticism", "content": "Elections are rigged", "platform": "twitter"},
        {"type": "join_protest", "content": "Democracy march", "platform": "telegram", "location": "Central Square"},
        {"type": "use_vpn", "platform": "network_monitor"},
        {"type": "contact_journalist", "content": "Whistleblower tip", "platform": "signal"},
        {"type": "follow_dissident", "content": "Followed @exiled_activist", "platform": "twitter"},
    ]:
        engine.process_social_media_activity("citizen-B", [activity])
    results["citizen_B_dissident"] = engine.get_citizen_profile("citizen-B")

    for activity in [
        {"type": "post_criticism", "content": "This policy is terrible", "platform": "weibo"},
        {"type": "delete_critical_post", "content": "Deleted: This policy is terrible", "platform": "weibo"},
        {"type": "share_govt_content", "content": "I support the new policy direction", "platform": "weibo"},
    ]:
        engine.process_social_media_activity("citizen-C", [activity])
    results["citizen_C_self_correcting"] = engine.get_citizen_profile("citizen-C")

    return jsonify(results)


# ── Gateway ──

def _require_api_key():
    key = request.headers.get("X-API-Key")
    if not key:
        return None, (jsonify({"error": "Missing X-API-Key header"}), 401)
    system = verify_api_key(key)
    if not system:
        return None, (jsonify({"error": "Invalid API key"}), 401)
    return system, None


@app.route("/gateway/check", methods=["POST"])
def gateway_check():
    system, err = _require_api_key()
    if err:
        return err
    data = request.json or {}
    citizen_id = sanitize_citizen_id(data.get("citizen_id", ""))
    service = data.get("service")
    action = data.get("action")
    if not citizen_id or not service:
        return jsonify({"error": "citizen_id and service are required"}), 400
    record = db.citizens.get(citizen_id)
    if not record:
        return jsonify({"error": "Citizen not found"}), 404
    result, error = check_privileges(record, service, action)
    if error:
        return jsonify({"error": error}), 400
    result["calling_system"] = system
    return jsonify(result)


@app.route("/gateway/citizen/<citizen_id>", methods=["GET"])
def gateway_citizen_lookup(citizen_id):
    system, err = _require_api_key()
    if err:
        return err
    citizen_id = sanitize_citizen_id(citizen_id)
    record = db.citizens.get(citizen_id)
    if not record:
        return jsonify({"error": "Citizen not found"}), 404
    return jsonify({
        "citizen_id": record["citizen_id"],
        "civic_score": record["civic_score"],
        "risk_tier": record["risk_tier"],
        "travel_status": record["travel_status"],
        "employment_clearance": record["employment_clearance"],
        "service_access": record["service_access"],
    })


@app.route("/gateway/services", methods=["GET"])
def gateway_services():
    system, err = _require_api_key()
    if err:
        return err
    return jsonify({"services": list(VALID_SERVICES), "tier_matrix": TIER_PRIVILEGES})


@app.route("/gateway/audit", methods=["GET"])
def gateway_audit():
    system, err = _require_api_key()
    if err:
        return err
    citizen_id = request.args.get("citizen_id")
    limit = request.args.get("limit", 50, type=int)
    return jsonify(get_audit_log(citizen_id=citizen_id, limit=limit))


@app.route("/gateway/register", methods=["POST"])
def gateway_register_system():
    data = request.json or {}
    system_name = data.get("system_name")
    if not system_name:
        return jsonify({"error": "system_name is required"}), 400
    key = generate_api_key(system_name)
    return jsonify({"system_name": system_name, "api_key": key}), 201


# ── Identity Graph / Network ──

@app.route("/graph/stats", methods=["GET"])
@require_auth
def graph_stats():
    return jsonify(graph_bridge.get_graph_stats())


@app.route("/graph/dashboard", methods=["GET"])
@require_auth
def graph_dashboard_data():
    return jsonify(graph_bridge.get_network_dashboard_data())


@app.route("/graph/relationships", methods=["GET"])
@require_auth
def get_relationships():
    citizen_id = request.args.get("citizen_id")
    if citizen_id:
        citizen_id = sanitize_citizen_id(citizen_id)
        return jsonify(db.get_citizen_relationships(citizen_id))
    return jsonify(db.get_all_relationships())


@app.route("/graph/relationships", methods=["POST"])
@require_auth
def add_relationship():
    data = request.json or {}

    # Bulk mode
    if "relationships" in data:
        rels = data["relationships"]
        for r in rels:
            r["citizen_a"] = sanitize_citizen_id(r.get("citizen_a", ""))
            r["citizen_b"] = sanitize_citizen_id(r.get("citizen_b", ""))
            # Ensure both citizens exist
            db.get_citizen(r["citizen_a"])
            db.get_citizen(r["citizen_b"])
        db.add_relationships_bulk(rels)
        graph_bridge.invalidate()
        return jsonify({"status": "created", "count": len(rels)}), 201

    # Single mode
    citizen_a = sanitize_citizen_id(data.get("citizen_a", ""))
    citizen_b = sanitize_citizen_id(data.get("citizen_b", ""))
    edge_type = data.get("edge_type", "weak_signal")
    weight = float(data.get("weight", 0.5))

    if not citizen_a or not citizen_b:
        return jsonify({"error": "citizen_a and citizen_b are required"}), 400
    if citizen_a == citizen_b:
        return jsonify({"error": "Cannot create self-relationship"}), 400
    if edge_type not in ("family", "coworker", "friend", "weak_signal"):
        return jsonify({"error": f"Invalid edge_type: {edge_type}"}), 400

    # Ensure both citizens exist in DB
    db.get_citizen(citizen_a)
    db.get_citizen(citizen_b)

    db.add_relationship(citizen_a, citizen_b, edge_type, weight)
    graph_bridge.invalidate()
    return jsonify({
        "status": "created",
        "citizen_a": citizen_a,
        "citizen_b": citizen_b,
        "edge_type": edge_type,
        "weight": weight,
    }), 201


@app.route("/graph/relationships", methods=["DELETE"])
@require_auth
def remove_relationship():
    data = request.json or {}
    citizen_a = sanitize_citizen_id(data.get("citizen_a", ""))
    citizen_b = sanitize_citizen_id(data.get("citizen_b", ""))
    if not citizen_a or not citizen_b:
        return jsonify({"error": "citizen_a and citizen_b are required"}), 400
    db.remove_relationship(citizen_a, citizen_b)
    graph_bridge.invalidate()
    return jsonify({"status": "removed"})


@app.route("/graph/citizen/<citizen_id>/network", methods=["GET"])
@require_auth
def citizen_network_analysis(citizen_id):
    citizen_id = sanitize_citizen_id(citizen_id)
    max_hops = request.args.get("max_hops", 3, type=int)
    try:
        analysis = graph_bridge.analyze_citizen_network(citizen_id, max_hops=max_hops)
        return jsonify({
            "citizen_id": analysis.citizen_id,
            "network_risk_score": analysis.network_risk_score,
            "influence_reach": analysis.influence_reach,
            "connection_count": analysis.connection_count,
            "avg_neighbor_risk": analysis.avg_neighbor_risk,
            "top_influencers": analysis.top_influencers,
            "top_influenced": analysis.top_influenced,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/graph/citizen/<citizen_id>/contagion", methods=["POST"])
@require_auth
def citizen_contagion(citizen_id):
    citizen_id = sanitize_citizen_id(citizen_id)
    data = request.json or {}
    propagation_factor = float(data.get("propagation_factor", 0.15))
    apply_effects = data.get("apply", False)

    try:
        contagion = graph_bridge.simulate_contagion(
            citizen_id, propagation_factor=propagation_factor,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    result = {
        "seed_citizen_id": contagion.seed_citizen_id,
        "affected_count": contagion.affected_count,
        "affected_fraction": contagion.affected_fraction,
        "influence_radius": contagion.influence_radius,
        "iterations": contagion.iterations,
        "affected_citizens": contagion.affected_citizens[:50],
        "score_adjustments": dict(list(contagion.score_adjustments.items())[:50]),
        "metrics": contagion.metrics,
    }

    if apply_effects and contagion.score_adjustments:
        applied = graph_bridge.apply_contagion(contagion)
        result["applied"] = applied
        result["status"] = "applied"
    else:
        result["status"] = "simulated"

    return jsonify(result)


@app.route("/graph/contagion/history", methods=["GET"])
@require_auth
def contagion_history():
    citizen_id = request.args.get("citizen_id")
    limit = request.args.get("limit", 20, type=int)
    if citizen_id:
        citizen_id = sanitize_citizen_id(citizen_id)
    return jsonify(db.get_contagion_events(citizen_id=citizen_id, limit=limit))


@app.route("/graph/citizen/<citizen_id>/dashboard.png", methods=["GET"])
@require_auth
def citizen_contagion_dashboard(citizen_id):
    import os
    import tempfile
    citizen_id = sanitize_citizen_id(citizen_id)
    save_path = os.path.join(tempfile.gettempdir(), f"contagion_{citizen_id}.png")
    try:
        graph_bridge.render_contagion_dashboard(citizen_id, save_path)
        return send_file(save_path, mimetype="image/png")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ── Dashboard ──

@app.route("/dashboard")
@require_auth
def dashboard():
    return render_template("dashboard.html")


@app.route("/reset", methods=["POST"])
@require_auth
def reset_data():
    db.reset()
    return jsonify({"status": "reset"})


# ── Error Handlers ──

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({"error": "Rate limit exceeded", "detail": str(e.description)}), 429


@app.errorhandler(500)
def internal_error(e):
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(debug=cfg.DEBUG, host=cfg.HOST, port=cfg.PORT)
