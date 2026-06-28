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
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
from urllib.parse import quote as requests_quote, urlparse

# Load .env before anything else
load_dotenv(Path(__file__).resolve().parent / ".env")

import shorts_generator.config as generator_config
from shorts_generator import generate_shorts
from shorts_generator.local.templates import list_templates, normalize_template_ids
from shorts_generator import social_publish


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR)).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
WEB_OUTPUT_DIR = Path(os.getenv("WEB_OUTPUT_DIR", DATA_DIR / "web_output")).resolve()
USERS_FILE = DATA_DIR / "users.json"
YOUTUBE_COOKIE_FILE = Path(os.getenv("YTDLP_COOKIE_FILE") or DATA_DIR / "youtube_cookies.txt").resolve()
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

def _normalize_email(email: str) -> str:
    return email.strip().lower()

def _username_from_email(email: str) -> str:
    base = email.split("@", 1)[0]
    username = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")
    return username or "user"

def _unique_username(base: str, users: Dict) -> str:
    username = base
    suffix = 2
    while username in users:
        username = f"{base}_{suffix}"
        suffix += 1
    return username

def _email_taken(email: str, users: Dict) -> bool:
    return any(_normalize_email(str(user.get("email", ""))) == email for user in users.values())

def _find_user_by_identifier(identifier: str, users: Dict):
    ident = identifier.strip().lower()
    if ident in users:
        return ident, users[ident]
    for username, user in users.items():
        if _normalize_email(str(user.get("email", ""))) == ident:
            return username, user
    return None, None

def _current_user_record() -> Dict:
    users = _load_users()
    return users.get(_current_user() or "", {})

def _current_user_is_admin() -> bool:
    return _current_user_record().get("role", "member") == "admin"

def _migrate_env_user() -> None:
    """On first run, migrate WEB_AUTH_USERNAME/PASSWORD into users.json."""
    env_user = os.getenv("WEB_AUTH_USERNAME", "").strip()
    env_pass = os.getenv("WEB_AUTH_PASSWORD", "").strip()
    if env_user and env_pass and not _users_exist():
        email = _normalize_email(env_user) if "@" in env_user else ""
        username = _username_from_email(email) if email else env_user.lower()
        users = {username: {
            "password": _hash_password(env_pass),
            "display": username.capitalize(),
            "email": email,
            "created_at": _utc_now(),
            "role": "admin",
        }}
        _save_users(users)
        print(f"[auth] Migrated env user '{username}' into users.json", flush=True)

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

def _is_youtube_url(source: str) -> bool:
    parsed = urlparse(source)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in {"youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"} or host.endswith(".youtube.com")

def _youtube_cookie_status() -> Dict:
    cookie_file_exists = YOUTUBE_COOKIE_FILE.exists() and YOUTUBE_COOKIE_FILE.stat().st_size > 0
    cookie_text_set = bool(os.getenv("YTDLP_COOKIES_TEXT", "").strip())
    browser_cookie_source = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    api_mode_ready = bool(
        generator_config.MUAPI_API_KEY
        and generator_config.WEB_PIPELINE_MODE in {"api", "auto"}
    )
    return {
        "ready": bool(cookie_file_exists or cookie_text_set or browser_cookie_source or api_mode_ready),
        "file_exists": cookie_file_exists,
        "file_path": str(YOUTUBE_COOKIE_FILE),
        "env_text": cookie_text_set,
        "browser": browser_cookie_source,
        "api_mode_ready": api_mode_ready,
        "updated_at": datetime.fromtimestamp(YOUTUBE_COOKIE_FILE.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
        if cookie_file_exists else None,
    }

def _youtube_setup_message() -> str:
    return (
        "YouTube download setup is required on this hosted server. "
        "Open Status and save YouTube cookies once, or configure MUAPI_API_KEY with WEB_PIPELINE_MODE=api."
    )

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
                try:
                    path.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
                except OSError:
                    pass
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
        pipeline_mode = generator_config.WEB_PIPELINE_MODE
        if pipeline_mode == "auto":
            pipeline_mode = "api" if generator_config.MUAPI_API_KEY and source.startswith(("http://", "https://")) else "local"
        if pipeline_mode not in {"api", "local"}:
            pipeline_mode = "local"
        if pipeline_mode == "api" and not generator_config.MUAPI_API_KEY:
            pipeline_mode = "local"

        with redirect_stdout(output), redirect_stderr(output):
            result = generate_shorts(
                youtube_url=source,
                num_clips=num_clips,
                aspect_ratio=aspect_ratio,
                download_format=download_format,
                language=language or None,
                mode=pipeline_mode,
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
        error_text = str(exc)
        youtube_setup_error = (
            error_text.startswith("YouTube blocked this server download")
            or error_text.startswith("YouTube rejected the server download")
        )
        if not youtube_setup_error:
            _append_log(job_id, output.getvalue())
        if os.getenv("WEB_SHOW_TRACEBACKS", "false").strip().lower() == "true":
            _append_log(job_id, traceback.format_exc())
        else:
            _append_log(job_id, f"ERROR: {error_text}")
        _update_job(
            job_id,
            status="failed",
            error=error_text,
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


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(_exc):
    max_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"File is too large. Maximum upload size is {max_mb} MB."}), 413


@app.get("/api/status")
@login_required
def app_status():
    youtube_status = _youtube_cookie_status()
    return jsonify({
        "status": "ok",
        "warnings": STARTUP_WARNINGS,
        "transcriber_provider": generator_config.TRANSCRIBER_PROVIDER,
        "llm_provider": generator_config.LLM_PROVIDER,
        "web_pipeline_mode": generator_config.WEB_PIPELINE_MODE,
        "youtube_download_ready": youtube_status["ready"],
        "youtube_download_setup_url": url_for("status_page", _anchor="setup-youtube-downloads"),
        "web_output_dir": str(WEB_OUTPUT_DIR),
        "template_count": len(list_templates()),
        "user": _current_user(),
    })


@app.get("/status")
@login_required
def status_page():
    social = social_publish.configured_platforms()
    with jobs_lock:
        job_count = len(jobs)
    youtube_status = _youtube_cookie_status()
    checks = [
        ("Web server", True, "Running"),
        ("Templates", len(list_templates()) > 0, f"{len(list_templates())} styles loaded"),
        ("YouTube downloads", youtube_status["ready"],
         "Ready" if youtube_status["ready"] else "Needs cookies or API mode"),
        ("Transcriber", True, generator_config.TRANSCRIBER_PROVIDER),
        ("AI highlights", generator_config.LLM_PROVIDER != "heuristic",
         generator_config.LLM_PROVIDER + (" (set OPENAI_API_KEY/GEMINI_API_KEY for AI)"
                                          if generator_config.LLM_PROVIDER == "heuristic" else "")),
        ("YouTube publishing", social.get("youtube"), "Connected app" if social.get("youtube") else "Not configured"),
        ("Instagram publishing", social.get("instagram"), "Connected app" if social.get("instagram") else "Not configured"),
        ("Facebook publishing", social.get("facebook"), "Connected app" if social.get("facebook") else "Not configured"),
    ]
    return render_template(
        "status.html",
        checks=checks,
        warnings=STARTUP_WARNINGS,
        job_count=job_count,
        output_dir=str(WEB_OUTPUT_DIR),
        user=_current_user(),
        is_admin=_current_user_is_admin(),
        youtube_status=youtube_status,
        youtube_message=request.args.get("youtube"),
        youtube_error=request.args.get("youtube_error"),
    )


@app.post("/settings/youtube-cookies")
@login_required
def save_youtube_cookies():
    if not _current_user_is_admin():
        return jsonify({"error": "Only workspace admins can update YouTube cookies."}), 403

    cookies_text = (request.form.get("cookies_text") or "").strip()
    if not cookies_text:
        return redirect(url_for("status_page", youtube_error="Paste YouTube cookies before saving.", _anchor="setup-youtube-downloads"))

    normalized = cookies_text.replace("\\n", "\n")
    if "youtube.com" not in normalized and ".youtube.com" not in normalized:
        return redirect(url_for("status_page", youtube_error="Those cookies do not look like YouTube cookies.", _anchor="setup-youtube-downloads"))

    YOUTUBE_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    YOUTUBE_COOKIE_FILE.write_text(normalized, encoding="utf-8")
    try:
        YOUTUBE_COOKIE_FILE.chmod(0o600)
    except OSError:
        pass
    return redirect(url_for("status_page", youtube="YouTube cookies saved. Try the link again.", _anchor="setup-youtube-downloads"))


@app.get("/api/me")
@login_required
def me():
    users = _load_users()
    user = users.get(_current_user() or "", {})
    return jsonify({
        "username": _current_user(),
        "email": user.get("email"),
        "display": session.get("display"),
        "role": user.get("role", "member"),
        "created_at": user.get("created_at"),
    })


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    success = None
    form_email = ""
    form_display = ""

    if request.method == "POST":
        email = _normalize_email(request.form.get("email") or "")
        requested_username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm  = request.form.get("confirm") or ""
        display  = (request.form.get("display") or (email.split("@", 1)[0] if email else "")).strip()
        form_email = email
        form_display = display

        if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            error = "Enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            users = _load_users()
            if _email_taken(email, users):
                error = "An account with this email already exists."
            else:
                username_base = requested_username or _username_from_email(email)
                if len(username_base) < 3:
                    username_base = f"{username_base}_user"
                if not re.match(r'^[a-z0-9_]+$', username_base):
                    error = "Username may only contain letters, numbers and underscores."
                    return render_template("login.html", mode="register", error=error, success=success,
                                           users_exist=_users_exist(),
                                           form_email=form_email, form_display=form_display)
                username = _unique_username(username_base, users)
                users[username] = {
                    "password": _hash_password(password),
                    "display": display,
                    "email": email,
                    "created_at": _utc_now(),
                    "role": "admin" if not users else "member",
                }
                _save_users(users)
                session.clear()
                session.permanent = True
                session["user"] = username
                session["display"] = display
                return redirect(url_for("index"))

    return render_template("login.html", mode="register", error=error, success=success,
                           users_exist=_users_exist(),
                           form_email=form_email, form_display=form_display)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _users_exist():
        return redirect(url_for("register"))
    if _is_authenticated():
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        identifier = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))
        users = _load_users()
        username, user = _find_user_by_identifier(identifier, users)
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
        max_upload_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
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

    if source_type == "url" and _is_youtube_url(source) and not _youtube_cookie_status()["ready"]:
        return jsonify({"error": _youtube_setup_message()}), 400

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


# NOTE: social_connections.json stores live OAuth tokens — treat as a secret.
SOCIAL_CONNECTIONS_FILE = DATA_DIR / "social_connections.json"
POST_ANALYTICS_FILE = DATA_DIR / "post_analytics.json"
SCHEDULED_POSTS_FILE = DATA_DIR / "scheduled_posts.json"
_social_lock = threading.Lock()
_schedule_lock = threading.Lock()


def _load_social_connections() -> Dict:
    if SOCIAL_CONNECTIONS_FILE.exists():
        try:
            return json.loads(SOCIAL_CONNECTIONS_FILE.read_text())
        except Exception:
            pass
    return {"youtube": None, "instagram": None, "facebook": None}


def _save_social_connections(data: Dict) -> None:
    SOCIAL_CONNECTIONS_FILE.write_text(json.dumps(data, indent=2))


def _public_connections() -> Dict:
    """Connections stripped of secrets, plus whether each provider is configured."""
    raw = _load_social_connections()
    configured = social_publish.configured_platforms()
    out = {}
    for platform in ("youtube", "instagram", "facebook"):
        out[platform] = {
            "configured": configured.get(platform, False),
            "connection": social_publish.public_summary(raw.get(platform)),
        }
    return out


def _load_post_analytics() -> list:
    if POST_ANALYTICS_FILE.exists():
        try:
            return json.loads(POST_ANALYTICS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_post_analytics(data: list) -> None:
    POST_ANALYTICS_FILE.write_text(json.dumps(data, indent=2))


def _load_scheduled_posts() -> list:
    if SCHEDULED_POSTS_FILE.exists():
        try:
            return json.loads(SCHEDULED_POSTS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_scheduled_posts(data: list) -> None:
    SCHEDULED_POSTS_FILE.write_text(json.dumps(data, indent=2))


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


def _oauth_redirect_uri(platform: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", request.host_url.rstrip("/"))
    return f"{base}/oauth/{platform}/callback"


@app.get("/api/social/connections")
@login_required
def get_social_connections():
    # Returns secret-free summaries plus per-platform "configured" flags.
    return jsonify(_public_connections())


@app.post("/api/social/connect")
@login_required
def connect_social():
    """Begin the real OAuth flow; returns the provider authorize URL."""
    data = request.get_json(silent=True) or {}
    platform = data.get("platform")
    if platform not in ("youtube", "instagram", "facebook"):
        return jsonify({"error": "Unknown platform"}), 400
    if not social_publish.provider_configured(platform):
        return jsonify({
            "error": "not_configured",
            "message": (
                f"{platform.title()} API credentials are not set on the server. "
                "See SOCIAL_SETUP.md to add OAuth credentials."
            ),
        }), 400
    state = secrets.token_urlsafe(24)
    session[f"oauth_state_{platform}"] = state
    try:
        url = social_publish.build_auth_url(platform, _oauth_redirect_uri(platform), state)
    except social_publish.SocialError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "auth_url": url})


@app.get("/oauth/<platform>/callback")
@login_required
def oauth_callback(platform: str):
    if platform not in ("youtube", "instagram", "facebook"):
        return redirect(url_for("index") + "#social")
    error = request.args.get("error")
    if error:
        return redirect(url_for("index") + f"#social?error={error}")
    state = request.args.get("state")
    expected = session.pop(f"oauth_state_{platform}", None)
    if not state or state != expected:
        return redirect(url_for("index") + "#social?error=state_mismatch")
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index") + "#social?error=no_code")
    try:
        conn = social_publish.exchange_code(platform, code, _oauth_redirect_uri(platform))
        with _social_lock:
            connections = _load_social_connections()
            connections[platform] = conn
            _save_social_connections(connections)
    except Exception as exc:  # noqa: BLE001 — surface any provider error to the UI
        return redirect(url_for("index") + f"#social?error={requests_quote(str(exc))}")
    return redirect(url_for("index") + "#social?connected=" + platform)


@app.post("/api/social/disconnect")
@login_required
def disconnect_social():
    data = request.get_json(silent=True) or {}
    platform = data.get("platform")
    if platform not in ("youtube", "instagram", "facebook"):
        return jsonify({"error": "Unknown platform"}), 400
    with _social_lock:
        connections = _load_social_connections()
        connections[platform] = None
        _save_social_connections(connections)
    return jsonify({"ok": True, "connections": _public_connections()})


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


@app.post("/api/analytics/sync")
@login_required
def sync_analytics():
    """Pull live metrics from connected platforms for every published post.

    Updates view/like/comment counts in post_analytics.json and re-runs the
    learning analysis so Insights and Compare reflect real numbers.
    """
    connections = _load_social_connections()
    posts = _load_post_analytics()
    updated = 0
    errors = []
    for post in posts:
        ext_id = post.get("external_id")
        platform = post.get("platform")
        conn = connections.get(platform)
        if not ext_id or not conn:
            continue
        try:
            metrics = social_publish.fetch_metrics(conn, ext_id)
            post.update({
                "views": metrics["views"],
                "likes": metrics["likes"],
                "comments": metrics["comments"],
                "shares": metrics["shares"],
                "synced_at": _utc_now(),
            })
            post["analysis"] = _analyze_post_performance(post)
            updated += 1
        except social_publish.SocialError as exc:
            errors.append(f"{platform}: {exc}")
    if updated:
        _save_post_analytics(posts)
    return jsonify({"ok": True, "updated": updated, "errors": errors, "posts": posts})


# ── Scheduling ─────────────────────────────────────────────────────────────────

@app.get("/api/schedule")
@login_required
def list_scheduled():
    return jsonify({"scheduled": _load_scheduled_posts()})


@app.post("/api/schedule")
@login_required
def create_scheduled():
    """Schedule a rendered reel to publish later on one or more platforms."""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    short_index = int(data.get("short_index") or 1)
    platforms = [p for p in (data.get("platforms") or []) if p in ("youtube", "instagram", "facebook")]
    publish_at = (data.get("publish_at") or "").strip()
    title = (data.get("title") or "Untitled reel")[:120]
    caption = (data.get("caption") or "")[:2200]
    if not platforms:
        return jsonify({"error": "Pick at least one platform"}), 400
    if not publish_at:
        return jsonify({"error": "Pick a publish date/time"}), 400

    # Resolve the rendered clip file (if a render job was chosen).
    clip_path = ""
    poster = ""
    if job_id:
        with jobs_lock:
            job = jobs.get(job_id)
        if job:
            shorts = (job.get("result") or {}).get("shorts") or []
            if 1 <= short_index <= len(shorts):
                short = shorts[short_index - 1]
                resolved = _media_path_from_value(job_id, short.get("clip_url"))
                clip_path = str(resolved) if resolved else ""
                poster = short.get("poster_media_url") or ""

    entry = {
        "id": uuid.uuid4().hex[:8],
        "job_id": job_id or "",
        "short_index": short_index,
        "clip_path": clip_path,
        "poster": poster,
        "platforms": platforms,
        "title": title,
        "caption": caption,
        "publish_at": publish_at,
        "status": "scheduled",        # scheduled → publishing → published / failed
        "created_at": _utc_now(),
        "results": {},
        "error": "",
    }
    with _schedule_lock:
        items = _load_scheduled_posts()
        items.insert(0, entry)
        _save_scheduled_posts(items)
    return jsonify({"ok": True, "scheduled": entry})


@app.delete("/api/schedule/<sched_id>")
@login_required
def delete_scheduled(sched_id: str):
    with _schedule_lock:
        items = _load_scheduled_posts()
        items = [s for s in items if s.get("id") != sched_id]
        _save_scheduled_posts(items)
    return jsonify({"ok": True})


@app.post("/api/schedule/<sched_id>/publish-now")
@login_required
def publish_now(sched_id: str):
    items = _load_scheduled_posts()
    entry = next((s for s in items if s.get("id") == sched_id), None)
    if not entry:
        return jsonify({"error": "Not found"}), 404
    _run_scheduled_publish(entry)
    return jsonify({"ok": True, "scheduled": entry})


def _public_clip_url(clip_path: str) -> Optional[str]:
    """Build an https URL Meta can fetch. Requires PUBLIC_BASE_URL for IG/FB."""
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base or not clip_path:
        return None
    p = Path(clip_path).resolve()
    try:
        rel = p.relative_to(WEB_OUTPUT_DIR)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 2:
        return f"{base}/media/{parts[0]}/{parts[-1]}"
    return None


def _run_scheduled_publish(entry: Dict) -> None:
    """Publish one scheduled entry to all its platforms; record results."""
    entry["status"] = "publishing"
    _persist_scheduled(entry)
    connections = _load_social_connections()
    public_url = _public_clip_url(entry.get("clip_path", ""))
    any_ok = False
    for platform in entry["platforms"]:
        conn = connections.get(platform)
        if not conn:
            entry["results"][platform] = {"ok": False, "error": "not connected"}
            continue
        try:
            res = social_publish.publish(
                conn,
                video_path=entry.get("clip_path", ""),
                title=entry["title"],
                caption=entry["caption"],
                public_video_url=public_url,
            )
            entry["results"][platform] = {"ok": True, "id": res["id"], "url": res["url"]}
            any_ok = True
            # Record it as a published post so analytics can be synced later.
            _record_published_post(entry, platform, res["id"])
        except Exception as exc:  # noqa: BLE001
            entry["results"][platform] = {"ok": False, "error": str(exc)}
    entry["status"] = "published" if any_ok else "failed"
    entry["published_at"] = _utc_now()
    _persist_scheduled(entry)


def _record_published_post(entry: Dict, platform: str, external_id: str) -> None:
    posts = _load_post_analytics()
    post = {
        "id": uuid.uuid4().hex[:8],
        "title": entry["title"],
        "platform": platform,
        "external_id": external_id,
        "posted_at": _utc_now(),
        "views": 0, "likes": 0, "comments": 0, "shares": 0, "score": 0,
        "template": "", "thumbnail": entry.get("poster", ""), "notes": "",
    }
    post["analysis"] = _analyze_post_performance(post)
    posts.insert(0, post)
    _save_post_analytics(posts)


def _persist_scheduled(entry: Dict) -> None:
    with _schedule_lock:
        items = _load_scheduled_posts()
        for i, s in enumerate(items):
            if s.get("id") == entry["id"]:
                items[i] = entry
                break
        _save_scheduled_posts(items)


def _scheduler_loop() -> None:
    """Background worker: every 60s, publish any scheduled post that is due."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            for entry in _load_scheduled_posts():
                if entry.get("status") != "scheduled":
                    continue
                when = entry.get("publish_at", "")
                try:
                    due = datetime.fromisoformat(when.replace("Z", "+00:00"))
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if due <= now:
                    _run_scheduled_publish(entry)
        except Exception:  # noqa: BLE001 — never let the loop die
            traceback.print_exc()
        threading.Event().wait(60)


@app.get("/api/calendar")
@login_required
def get_calendar():
    posts = _load_post_analytics()
    scheduled = _load_scheduled_posts()
    with jobs_lock:
        job_list = list(jobs.values())
    events = []
    # Published / tracked posts (live performance).
    for post in posts:
        events.append({
            "id": post["id"],
            "type": "post",
            "title": post["title"],
            "date": post.get("posted_at", "")[:10],
            "platform": post.get("platform", ""),
            "views": post.get("views", 0),
            "verdict": (post.get("analysis") or {}).get("verdict", ""),
            "external_id": post.get("external_id", ""),
        })
    # Upcoming scheduled posts.
    for s in scheduled:
        events.append({
            "id": s["id"],
            "type": "scheduled",
            "title": s.get("title", "Scheduled reel"),
            "date": s.get("publish_at", "")[:10],
            "time": s.get("publish_at", "")[11:16],
            "platforms": s.get("platforms", []),
            "status": s.get("status", "scheduled"),
        })
    # Render jobs (production activity).
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


# Start the scheduler thread once, at import time.
threading.Thread(target=_scheduler_loop, daemon=True).start()


if __name__ == "__main__":
    WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() == "true"
    app.run(host=os.getenv("WEB_HOST", "127.0.0.1"), port=int(os.getenv("WEB_PORT", "7860")), debug=debug)
