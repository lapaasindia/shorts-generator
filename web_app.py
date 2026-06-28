"""Web UI for AI YouTube Shorts Generator."""
import hmac
import io
import json
import os
import re
import threading
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Dict, Optional

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

import shorts_generator.config as generator_config
from shorts_generator import generate_shorts
from shorts_generator.local.templates import list_templates, normalize_template_ids


BASE_DIR = Path(__file__).resolve().parent
WEB_OUTPUT_DIR = Path(os.getenv("WEB_OUTPUT_DIR", BASE_DIR / "web_output")).resolve()
UPLOAD_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".webm",
    ".avi",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32))
app.config.update(
    MAX_CONTENT_LENGTH=int(os.getenv("WEB_MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").strip().lower() == "true",
)

WEB_AUTH_USERNAME = os.getenv("WEB_AUTH_USERNAME", "team")
WEB_AUTH_PASSWORD = os.getenv("WEB_AUTH_PASSWORD", "").strip()
SECRET_KEY_CONFIGURED = bool(os.getenv("SECRET_KEY", "").strip())

jobs: Dict[str, Dict] = {}
jobs_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _auth_enabled() -> bool:
    return bool(WEB_AUTH_PASSWORD)


def _startup_warnings() -> list[str]:
    warnings = []
    if not _auth_enabled():
        warnings.append("WEB_AUTH_PASSWORD is not set; the web app is open to anyone who can reach it.")
    if _auth_enabled() and not SECRET_KEY_CONFIGURED:
        warnings.append("SECRET_KEY is not set; sessions will reset when the process restarts.")
    if generator_config.TRANSCRIBER_PROVIDER == "sarvam" and not generator_config.SARVAM_API_KEY:
        warnings.append("SARVAM_API_KEY is not set; Sarvam transcription jobs will fail.")
    if generator_config.LLM_PROVIDER == "openai" and not generator_config.OPENAI_API_KEY:
        warnings.append("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
    if generator_config.LLM_PROVIDER == "gemini" and not generator_config.GEMINI_API_KEY:
        warnings.append("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.")
    return warnings


STARTUP_WARNINGS = _startup_warnings()
for warning in STARTUP_WARNINGS:
    print(f"[web/startup] WARNING: {warning}", flush=True)


def _is_authenticated() -> bool:
    if not _auth_enabled():
        return True
    return bool(session.get("authenticated"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if _is_authenticated():
            return view(*args, **kwargs)
        return redirect(url_for("login", next=request.path))

    return wrapped


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


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/api/status")
@login_required
def app_status():
    return jsonify(
        {
            "status": "ok",
            "warnings": STARTUP_WARNINGS,
            "transcriber_provider": generator_config.TRANSCRIBER_PROVIDER,
            "llm_provider": generator_config.LLM_PROVIDER,
            "web_output_dir": str(WEB_OUTPUT_DIR),
            "template_count": len(list_templates()),
        }
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_enabled():
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        username_ok = hmac.compare_digest(username, WEB_AUTH_USERNAME)
        password_ok = hmac.compare_digest(password, WEB_AUTH_PASSWORD)
        if username_ok and password_ok:
            session.clear()
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Invalid login."

    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login" if _auth_enabled() else "index"))


@app.get("/")
@login_required
def index():
    return render_template(
        "index.html",
        auth_enabled=_auth_enabled(),
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


if __name__ == "__main__":
    WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() == "true"
    app.run(host=os.getenv("WEB_HOST", "127.0.0.1"), port=int(os.getenv("WEB_PORT", "7860")), debug=debug)
