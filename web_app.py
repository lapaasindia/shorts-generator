"""Web UI for AI YouTube Shorts Generator."""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

# Load .env before anything else
load_dotenv(Path(__file__).resolve().parent / ".env")

import shorts_generator.config as generator_config
from shorts_generator import generate_shorts
from shorts_generator.local.templates import list_templates, normalize_template_ids


BASE_DIR = Path(__file__).resolve().parent
WEB_OUTPUT_DIR = Path(os.getenv("WEB_OUTPUT_DIR", BASE_DIR / "web_output")).resolve()
USERS_FILE = BASE_DIR / "users.json"
UPLOAD_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi",
    ".mp3", ".wav", ".m4a", ".aac",
}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=int(os.getenv("WEB_MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").strip().lower() == "true",
    PERMANENT_SESSION_LIFETIME=86400 * 30,  # 30 days
)

jobs: Dict[str, Dict] = {}
jobs_lock = threading.Lock()

# ── User account helpers ──────────────────────────────────────────────────────

def _hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{hashed}"

def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, hashed = stored.split(":", 1)
        return hmac.compare_digest(
            hashlib.sha256((salt + password).encode()).hexdigest(),
            hashed,
        )
    except Exception:
        return False

def _load_users() -> Dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_users(users: Dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")

def _users_exist() -> bool:
    return bool(_load_users())

def _migrate_env_user() -> None:
    """On first run, migrate WEB_AUTH_USERNAME/PASSWORD into users.json."""
    env_user = os.getenv("WEB_AUTH_USERNAME", "").strip()
    env_pass = os.getenv("WEB_AUTH_PASSWORD", "").strip()
    if env_user and env_pass and not _users_exist():
        users = {env_user: {"password": _hash_password(env_pass), "created_at": _utc_now(), "role": "admin"}}
        _save_users(users)
        print(f"[auth] Migrated env user '{env_user}' into users.json", flush=True)

# ── Auth helpers ──────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _is_authenticated() -> bool:
    return bool(session.get("user"))

def _current_user() -> Optional[str]:
    return session.get("user")

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _users_exist():
            return redirect(url_for("register"))
        if _is_authenticated():
            return view(*args, **kwargs)
        return redirect(url_for("login", next=request.path))
    return wrapped

# ── Startup ───────────────────────────────────────────────────────────────────

def _startup_warnings() -> list:
    warnings = []
    if generator_config.TRANSCRIBER_PROVIDER == "sarvam" and not generator_config.SARVAM_API_KEY:
        warnings.append("SARVAM_API_KEY is not set; Sarvam transcription jobs will fail.")
    if generator_config.LLM_PROVIDER == "openai" and not generator_config.OPENAI_API_KEY:
        warnings.append("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
    if generator_config.LLM_PROVIDER == "gemini" and not generator_config.GEMINI_API_KEY:
        warnings.append("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.")
    return warnings

_migrate_env_user()
STARTUP_WARNINGS = _startup_warnings()
for _w in STARTUP_WARNINGS:
    print(f"[web/startup] WARNING: {_w}", flush=True)


@app.after_request
def _no_cache_html(response):
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _job_dir(job_id: str) -> Path:
    return WEB_OUTPUT_DIR / job_id


def _scrub_text(value: str) -> str:
    cleaned = value
    for path in (str(WEB_OUTPUT_DIR), str(BASE_DIR), str(Path.home())):
        if path:
            cleaned = cleaned.replace(path, "[server-path]")
    return re.sub(r"/Users/[^\s\"']+", "[server-path]", cleaned)


def _scrub_job_for_response(job: Dict) -> Dict:
    scrubbed = json.loads(json.dumps(job, default=str))
    job_id = scrubbed.get("id")
    if isinstance(job_id, str):
        shorts = (scrubbed.get("result") or {}).get("shorts") or []
        for index, short in enumerate(shorts, 1):
            if not short.get("poster_media_url"):
                short["poster_media_url"] = _ensure_poster(job_id, short.get("clip_url"), index)
    scrubbed["logs"] = [_scrub_text(str(line)) for line in scrubbed.get("logs", [])]
    if scrubbed.get("error"):
        scrubbed["error"] = _scrub_text(str(scrubbed["error"]))
    return scrubbed


def _job_meta_path(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _save_job_unlocked(job: Dict) -> None:
    job_id = job.get("id")
    if not job_id:
        return
    path = _job_meta_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")


def _load_jobs_from_disk() -> None:
    WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in WEB_OUTPUT_DIR.glob("*/job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_id = job.get("id")
        if isinstance(job_id, str):
            if job.get("status") in {"queued", "running"}:
                job["status"] = "interrupted"
                job["error"] = "Server restarted before this job finished."
            jobs[job_id] = job


def _job_summary(job: Dict) -> Dict:
    result = job.get("result") or {}
    shorts = result.get("shorts") or []
    reels = result.get("reels") or []
    first_short = shorts[0] if shorts else {}
    job_id = job.get("id")
    poster_url = first_short.get("poster_media_url")
    if not poster_url and isinstance(job_id, str):
        poster_url = _ensure_poster(job_id, first_short.get("clip_url"), 1)
    return {
        "id": job_id,
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "source_type": job.get("source_type"),
        "source_label": job.get("source_label"),
        "short_count": len(shorts),
        "reel_count": len(reels),
        "title": first_short.get("title"),
        "poster_media_url": poster_url,
        "error": job.get("error"),
    }


_load_jobs_from_disk()


def _public_media_url(job_id: str, path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path

    media_path = Path(path).resolve()
    try:
        media_path.relative_to(_job_dir(job_id).resolve())
    except ValueError:
        return None
    return f"/media/{job_id}/{media_path.name}"


def _media_path_from_value(job_id: str, value: Optional[str]) -> Optional[Path]:
    if not value or value.startswith(("http://", "https://")):
        return None

    job_dir = _job_dir(job_id).resolve()
    if value.startswith(f"/media/{job_id}/"):
        candidate = (job_dir / Path(value).name).resolve()
    else:
        candidate = Path(value).resolve()

    try:
        candidate.relative_to(job_dir)
    except ValueError:
        return None
    if not candidate.exists():
        return None
    return candidate


def _ensure_poster(job_id: str, video_value: Optional[str], index: int) -> Optional[str]:
    video_path = _media_path_from_value(job_id, video_value)
    if not video_path:
        return None

    poster_path = _job_dir(job_id) / f"poster_{index:02d}.jpg"
    if poster_path.exists():
        return f"/media/{job_id}/{poster_path.name}"

    try:
        import cv2  # type: ignore
    except ImportError:
        return None

    cap = cv2.VideoCapture(str(video_path))
    try:
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        positions = [0, 24, int(frames * 0.1), int(frames * 0.25), int(frames * 0.5)]
        for frame_index in positions:
            if frames:
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(frames - 1, frame_index)))
            ok, frame = cap.read()
            if not ok:
                continue
            if float(frame.std()) < 4.0:
                continue
            if cv2.imwrite(str(poster_path), frame):
                return f"/media/{job_id}/{poster_path.name}"
    finally:
        cap.release()
    return None


def _serialize_result(job_id: str, result: Dict) -> Dict:
    data = json.loads(json.dumps(result, default=str))
    source_url = _public_media_url(job_id, data.get("source_video_url"))
    data["source_video_media_url"] = source_url
    if source_url:
        data["source_video_url"] = source_url
    for index, short in enumerate(data.get("shorts", []), 1):
        clip_url = _public_media_url(job_id, short.get("clip_url"))
        poster_url = _ensure_poster(job_id, short.get("clip_url"), index)
        short["clip_media_url"] = clip_url
        short["poster_media_url"] = poster_url
        if clip_url:
            short["clip_url"] = clip_url
    return data


def _append_log(job_id: str, message: str) -> None:
    lines = [_scrub_text(line) for line in message.splitlines() if line.strip()]
    if not lines:
        return
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["logs"].extend(lines)
        job["logs"] = job["logs"][-80:]
        _save_job_unlocked(job)


def _update_job(job_id: str, **fields) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            job.update(fields)
            _save_job_unlocked(job)


def _run_job(
    job_id: str,
    source: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
    template_ids: list[str],
    nonlinear_edit: bool,
    focus_prompt: Optional[str],
    upscale: bool,
) -> None:
    job_dir = _job_dir(job_id)

    _update_job(job_id, status="running", started_at=_utc_now())
    _append_log(job_id, "Job started")

    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            result = generate_shorts(
                youtube_url=source,
                num_clips=num_clips,
                aspect_ratio=aspect_ratio,
                download_format=download_format,
                language=language or None,
                mode="local",
                output_dir=str(job_dir),
                template_ids=template_ids,
                nonlinear_edit=nonlinear_edit,
                focus_prompt=focus_prompt,
                upscale=upscale,
            )
        serialized_result = _serialize_result(job_id, result)
        job_result_path = job_dir / "result.json"
        job_result_path.write_text(json.dumps(serialized_result, indent=2, default=str), encoding="utf-8")
        _append_log(job_id, output.getvalue())
        _update_job(
            job_id,
            status="complete",
            result=serialized_result,
            result_json_url=f"/media/{job_id}/result.json",
            completed_at=_utc_now(),
        )
        _append_log(job_id, "Job complete")
    except Exception as exc:
        _append_log(job_id, output.getvalue())
        _append_log(job_id, traceback.format_exc())
        _update_job(
            job_id,
            status="failed",
            error=str(exc),
            completed_at=_utc_now(),
        )


@app.route("/manifest.json")
def serve_manifest():
    response = make_response(send_from_directory(app.static_folder, "manifest.json"))
    response.headers["Content-Type"] = "application/json"
    return response


@app.route("/sw.js")
def serve_sw():
    response = make_response(send_from_directory(app.static_folder, "sw.js"))
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/api/status")
@login_required
def app_status():
    return jsonify({
        "status": "ok",
        "warnings": STARTUP_WARNINGS,
        "transcriber_provider": generator_config.TRANSCRIBER_PROVIDER,
        "llm_provider": generator_config.LLM_PROVIDER,
        "web_output_dir": str(WEB_OUTPUT_DIR),
        "template_count": len(list_templates()),
        "user": _current_user(),
    })


@app.get("/api/me")
@login_required
def me():
    users = _load_users()
    user = users.get(_current_user() or "", {})
    return jsonify({
        "username": _current_user(),
        "display": session.get("display"),
        "role": user.get("role", "member"),
        "created_at": user.get("created_at"),
    })


@app.route("/register", methods=["GET", "POST"])
def register():
    # Only allow registration if NO users exist yet (first-time setup)
    if _users_exist() and not _is_authenticated():
        return redirect(url_for("login"))

    error = None
    success = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm  = request.form.get("confirm") or ""
        display  = (request.form.get("display") or username).strip()

        if not username or len(username) < 3:
            error = "Username must be at least 3 characters."
        elif not re.match(r'^[a-z0-9_]+$', username):
            error = "Username may only contain letters, numbers and underscores."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            users = _load_users()
            if username in users:
                error = "Username already taken."
            else:
                users[username] = {
                    "password": _hash_password(password),
                    "display": display,
                    "created_at": _utc_now(),
                    "role": "admin" if not users else "member",
                }
                _save_users(users)
                session.clear()
                session.permanent = True
                session["user"] = username
                session["display"] = display
                return redirect(url_for("index"))

    return render_template("login.html", mode="register", error=error, success=success)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _users_exist():
        return redirect(url_for("register"))
    if _is_authenticated():
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))
        users = _load_users()
        user = users.get(username)
        if user and _verify_password(password, user["password"]):
            session.clear()
            session.permanent = remember
            session["user"] = username
            session["display"] = user.get("display", username)
            next_url = request.args.get("next") or url_for("index")
            # Prevent open redirect
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
        error = "Incorrect username or password."

    return render_template("login.html", mode="login", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return render_template(
        "index.html",
        auth_enabled=True,
        current_user=_current_user(),
        display_name=session.get("display", _current_user()),
        reel_templates=list_templates(),
    )


@app.get("/api/templates")
@login_required
def templates_api():
    return jsonify({"templates": list_templates()})


@app.post("/jobs")
@login_required
def create_job():
    source_type = request.form.get("source_type", "url")
    video_url = (request.form.get("video_url") or "").strip()
    language = (request.form.get("language") or "").strip()
    aspect_ratio = request.form.get("aspect_ratio", "9:16")
    download_format = request.form.get("download_format", "720")
    template_ids = normalize_template_ids(request.form.getlist("template_ids"))
    nonlinear_edit = request.form.get("nonlinear_edit") == "on"
    upscale = request.form.get("upscale") == "on"
    focus_prompt = (request.form.get("focus_prompt") or "").strip()[:500]

    try:
        num_clips = max(1, int(request.form.get("num_clips", "3")))
    except ValueError:
        num_clips = 3

    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    source = video_url
    if source_type == "upload":
        upload = request.files.get("video_file")
        if not upload or not upload.filename:
            return jsonify({"error": "Choose a video file."}), 400

        filename = secure_filename(upload.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in UPLOAD_EXTENSIONS:
            return jsonify({"error": "Unsupported file type."}), 400

        upload_path = job_dir / f"source{suffix}"
        upload.save(upload_path)
        source = str(upload_path)
    elif not source:
        return jsonify({"error": "Enter a video URL."}), 400

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": _utc_now(),
            "logs": ["Queued"],
            "source_type": source_type,
            "source_label": filename if source_type == "upload" else video_url,
            "settings": {
                "num_clips": num_clips,
                "aspect_ratio": aspect_ratio,
                "download_format": download_format,
                "language": language,
                "template_ids": template_ids,
                "nonlinear_edit": nonlinear_edit,
                "focus_prompt": focus_prompt,
                "upscale": upscale,
            },
        }
        _save_job_unlocked(jobs[job_id])

    thread = threading.Thread(
        target=_run_job,
        args=(
            job_id,
            source,
            num_clips,
            aspect_ratio,
            download_format,
            language,
            template_ids,
            nonlinear_edit,
            focus_prompt,
            upscale,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status_url": f"/api/jobs/{job_id}"})


@app.get("/api/jobs")
@login_required
def list_jobs():
    with jobs_lock:
        latest = sorted(
            jobs.values(),
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        return jsonify({"jobs": [_job_summary(job) for job in latest[:30]]})


@app.get("/api/jobs/<job_id>")
@login_required
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        return jsonify(_scrub_job_for_response(job))


@app.get("/media/<job_id>/<path:filename>")
@login_required
def media(job_id: str, filename: str):
    job_dir = _job_dir(job_id).resolve()
    return send_from_directory(job_dir, filename, as_attachment=False)


SOCIAL_CONNECTIONS_FILE = BASE_DIR / "social_connections.json"
POST_ANALYTICS_FILE = BASE_DIR / "post_analytics.json"


def _load_social_connections() -> Dict:
    if SOCIAL_CONNECTIONS_FILE.exists():
        try:
            return json.loads(SOCIAL_CONNECTIONS_FILE.read_text())
        except Exception:
            pass
    return {"youtube": None, "instagram": None, "facebook": None}


def _save_social_connections(data: Dict) -> None:
    SOCIAL_CONNECTIONS_FILE.write_text(json.dumps(data, indent=2))


def _load_post_analytics() -> list:
    if POST_ANALYTICS_FILE.exists():
        try:
            return json.loads(POST_ANALYTICS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_post_analytics(data: list) -> None:
    POST_ANALYTICS_FILE.write_text(json.dumps(data, indent=2))


def _analyze_post_performance(post: Dict) -> Dict:
    views = post.get("views", 0)
    likes = post.get("likes", 0)
    comments = post.get("comments", 0)
    shares = post.get("shares", 0)
    score = post.get("score", 0)

    total_engagement = likes + comments * 2 + shares * 3
    engagement_rate = round((total_engagement / max(views, 1)) * 100, 2)

    learnings = []
    flags = []

    if views >= 100000:
        learnings.append("Strong hook in first 3 seconds drove high click-through.")
        learnings.append("Title contains emotional trigger word (surprise/shock/transformation).")
        learnings.append("Optimal length 30-60s maximized watch completion rate.")
        learnings.append("Trending audio or sound used — boosted algorithmic distribution.")
    elif views >= 10000:
        learnings.append("Solid mid-tier performance — good hook but caption could be stronger.")
        learnings.append("Engagement ratio healthy; resharing potential present.")
    else:
        flags.append("Low view count — possible weak hook or poor first frame thumbnail.")
        flags.append("Check if caption includes call-to-action or question to drive comments.")
        flags.append("Consider reposting with A/B tested thumbnail or different caption angle.")
        flags.append("Audio trend relevance may be low — try pairing with viral sound.")

    if engagement_rate >= 8:
        learnings.append(f"Exceptional engagement rate {engagement_rate}% — content resonated deeply with audience.")
    elif engagement_rate >= 3:
        learnings.append(f"Healthy engagement rate {engagement_rate}% — audience connected with topic.")
    else:
        flags.append(f"Low engagement rate {engagement_rate}% — content may not be evoking a reaction.")

    if shares >= views * 0.02:
        learnings.append("High share rate signals content is 'identity-shareable' — viewers want others to see this.")

    virality_score = min(100, int(
        (min(views, 1000000) / 10000) * 0.5 +
        engagement_rate * 3 +
        (shares / max(views, 1)) * 200
    ))

    return {
        "engagement_rate": engagement_rate,
        "virality_score": virality_score,
        "learnings": learnings,
        "flags": flags,
        "verdict": "viral" if views >= 100000 else ("growing" if views >= 10000 else "underperforming"),
    }


@app.get("/api/social/connections")
@login_required
def get_social_connections():
    return jsonify(_load_social_connections())


@app.post("/api/social/connect")
@login_required
def connect_social():
    data = request.get_json(silent=True) or {}
    platform = data.get("platform")
    handle = (data.get("handle") or "").strip()
    if platform not in ("youtube", "instagram", "facebook"):
        return jsonify({"error": "Unknown platform"}), 400
    connections = _load_social_connections()
    connections[platform] = {
        "handle": handle,
        "connected_at": _utc_now(),
        "status": "connected",
    }
    _save_social_connections(connections)
    return jsonify({"ok": True, "connections": connections})


@app.post("/api/social/disconnect")
@login_required
def disconnect_social():
    data = request.get_json(silent=True) or {}
    platform = data.get("platform")
    if platform not in ("youtube", "instagram", "facebook"):
        return jsonify({"error": "Unknown platform"}), 400
    connections = _load_social_connections()
    connections[platform] = None
    _save_social_connections(connections)
    return jsonify({"ok": True, "connections": connections})


@app.get("/api/analytics/posts")
@login_required
def get_analytics_posts():
    return jsonify({"posts": _load_post_analytics()})


@app.post("/api/analytics/posts")
@login_required
def add_analytics_post():
    data = request.get_json(silent=True) or {}
    posts = _load_post_analytics()
    post = {
        "id": uuid.uuid4().hex[:8],
        "title": (data.get("title") or "Untitled post")[:120],
        "platform": data.get("platform", "youtube"),
        "posted_at": data.get("posted_at") or _utc_now(),
        "views": max(0, int(data.get("views") or 0)),
        "likes": max(0, int(data.get("likes") or 0)),
        "comments": max(0, int(data.get("comments") or 0)),
        "shares": max(0, int(data.get("shares") or 0)),
        "score": max(0, int(data.get("score") or 0)),
        "template": data.get("template") or "",
        "thumbnail": data.get("thumbnail") or "",
        "notes": (data.get("notes") or "")[:500],
    }
    post["analysis"] = _analyze_post_performance(post)
    posts.insert(0, post)
    _save_post_analytics(posts)
    return jsonify({"ok": True, "post": post})


@app.delete("/api/analytics/posts/<post_id>")
@login_required
def delete_analytics_post(post_id: str):
    posts = _load_post_analytics()
    posts = [p for p in posts if p.get("id") != post_id]
    _save_post_analytics(posts)
    return jsonify({"ok": True})


@app.get("/api/analytics/compare")
@login_required
def compare_posts():
    id_a = request.args.get("a")
    id_b = request.args.get("b")
    posts = {p["id"]: p for p in _load_post_analytics()}
    post_a = posts.get(id_a)
    post_b = posts.get(id_b)
    if not post_a or not post_b:
        return jsonify({"error": "One or both posts not found"}), 404
    return jsonify({
        "a": post_a,
        "b": post_b,
        "analysis_a": _analyze_post_performance(post_a),
        "analysis_b": _analyze_post_performance(post_b),
    })


@app.get("/api/calendar")
@login_required
def get_calendar():
    posts = _load_post_analytics()
    with jobs_lock:
        job_list = list(jobs.values())
    events = []
    for post in posts:
        events.append({
            "id": post["id"],
            "type": "post",
            "title": post["title"],
            "date": post.get("posted_at", "")[:10],
            "platform": post.get("platform", ""),
            "views": post.get("views", 0),
            "verdict": (post.get("analysis") or {}).get("verdict", ""),
        })
    for job in job_list:
        date_str = (job.get("completed_at") or job.get("created_at") or "")[:10]
        if date_str:
            events.append({
                "id": job["id"],
                "type": "render",
                "title": job.get("source_label") or "Render job",
                "date": date_str,
                "status": job.get("status"),
            })
    return jsonify({"events": events})


if __name__ == "__main__":
    WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() == "true"
    app.run(host=os.getenv("WEB_HOST", "127.0.0.1"), port=int(os.getenv("WEB_PORT", "7860")), debug=debug)
