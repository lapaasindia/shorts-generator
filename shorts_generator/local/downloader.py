"""Local YouTube download via yt-dlp.

Returns a local mp4 path so the rest of the local pipeline can read it
directly off disk.
"""
import os
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from typing import Optional

from ..config import LOCAL_OUTPUT_DIR


def _import_ytdlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e
    return yt_dlp


def _ffmpeg_location() -> Optional[str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    try:
        import imageio_ffmpeg  # type: ignore
    except ImportError:
        return None

    return imageio_ffmpeg.get_ffmpeg_exe()


def _format_for(fmt: str) -> str:
    """Map our '720' / '1080' shorthand to a yt-dlp format selector."""
    try:
        height = int(fmt)
    except ValueError:
        height = 720
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={height}][ext=mp4]/best"
    )


def _format_candidates(fmt: str) -> list[tuple[str, str]]:
    """Return progressively looser yt-dlp selectors for videos with limited formats."""
    try:
        height = int(fmt)
    except ValueError:
        height = 720
    return [
        ("mp4", _format_for(fmt)),
        (
            "any <= requested height",
            f"bestvideo*[height<={height}]+bestaudio/best[height<={height}]/best",
        ),
        ("best available", "bestvideo*+bestaudio/best"),
    ]


def _is_format_unavailable_error(message: str) -> bool:
    needle = message.lower()
    return "requested format is not available" in needle or "format is not available" in needle


def _downloaded_path(ydl, info: dict) -> str:
    candidates = []
    for item in info.get("requested_downloads") or []:
        value = item.get("filepath") or item.get("_filename")
        if value:
            candidates.append(value)
    candidates.append(ydl.prepare_filename(info))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    # merge_output_format may rename the extension after merge.
    for candidate in candidates:
        stem, _ = os.path.splitext(candidate)
        for ext in (".mp4", ".mkv", ".webm"):
            merged = stem + ext
            if os.path.exists(merged):
                return merged

    return candidates[-1]


def _extract_youtube_video_id(source: str) -> Optional[str]:
    """Best-effort extraction of a YouTube video id from a URL."""
    parsed = urlparse(source)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        return video_id or None

    if "youtube.com" in host:
        if parsed.path.startswith("/watch"):
            qs = parse_qs(parsed.query)
            video_id = qs.get("v", [""])[0]
            return video_id or None
        match = re.search(r"/(?:shorts|embed|live)/([^/?#&]+)", parsed.path)
        if match:
            return match.group(1)

    return None


def _resolve_local_path(source: str) -> Optional[str]:
    """Return a local filesystem path if the input already points at one."""
    parsed = urlparse(source)
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            raw_path = f"//{parsed.netloc}{raw_path}"
        candidate = Path(raw_path).expanduser()
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
        raise RuntimeError(f"Local file URL does not exist: {source}")

    if parsed.scheme in ("http", "https"):
        return None

    candidate = Path(source).expanduser()
    if candidate.exists() and candidate.is_file():
        return str(candidate.resolve())

    if any(sep in source for sep in (os.sep, "/")) or source.startswith("~") or source.startswith("."):
        raise RuntimeError(f"Local file path does not exist: {source}")

    return None


def _existing_download(out_dir: str, video_id: str) -> Optional[str]:
    """Return a cached download path if we already have this YouTube id."""
    for ext in (".mp4", ".mkv", ".webm"):
        candidate = os.path.join(out_dir, f"source_{video_id}{ext}")
        if os.path.exists(candidate):
            return candidate
    return None


def _cookie_file_from_env(out_dir: str) -> Optional[str]:
    """Resolve a yt-dlp cookies file from file path or raw env text."""
    cookie_files = []
    configured_cookie_file = os.getenv("YTDLP_COOKIE_FILE", "").strip()
    if configured_cookie_file:
        cookie_files.append(configured_cookie_file)

    data_dir = os.getenv("DATA_DIR", "").strip()
    if data_dir:
        default_cookie_file = str(Path(data_dir) / "youtube_cookies.txt")
        if default_cookie_file not in cookie_files:
            cookie_files.append(default_cookie_file)

    for cookie_file in cookie_files:
        if os.path.exists(cookie_file):
            return cookie_file
        if cookie_file == configured_cookie_file:
            print(f"[download/local] YTDLP_COOKIE_FILE does not exist: {cookie_file}", flush=True)

    cookies_text = os.getenv("YTDLP_COOKIES_TEXT", "").strip()
    if not cookies_text:
        return None

    cookie_path = Path(out_dir) / "youtube_cookies.txt"
    # Hosted platforms often store multiline secrets with escaped newlines.
    normalized = cookies_text.replace("\\n", "\n")
    cookie_path.write_text(normalized, encoding="utf-8")
    try:
        cookie_path.chmod(0o600)
    except OSError:
        pass
    return str(cookie_path)


def _is_youtube_auth_error(message: str) -> bool:
    needle = message.lower()
    return (
        "sign in to confirm" in needle
        or "not a bot" in needle
        or "cookies" in needle and "youtube" in needle
        or "confirm you're not a bot" in needle
    )


def _youtube_auth_help(has_cookies: bool) -> str:
    if has_cookies:
        return (
            "YouTube rejected the server download even though cookies are configured. "
            "Refresh the YouTube cookies, or switch Status > YouTube downloads to API mode "
            "with a MuAPI key so users do not need YouTube cookies."
        )
    return (
        "YouTube blocked this server download with a bot/sign-in check. "
        "For hosted deployments, export YouTube cookies and set YTDLP_COOKIES_TEXT "
        "or mount a cookies.txt file and set YTDLP_COOKIE_FILE. "
        "The easiest hosted fix is Status > YouTube downloads > API mode with a MuAPI key. "
        "For local desktop runs, you can also set YTDLP_COOKIES_FROM_BROWSER=chrome. "
        "The page theme is unchanged; this is a server-side YouTube authentication requirement."
    )


def download_youtube_local(video_url: str, fmt: str = "720", out_dir: Optional[str] = None) -> str:
    """Download a remote URL or return a local file path unchanged."""
    local_path = _resolve_local_path(video_url)
    if local_path:
        print(f"[download/local] using local file: {local_path}", flush=True)
        return local_path

    yt_dlp = _import_ytdlp()
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    video_id = _extract_youtube_video_id(video_url)
    if video_id:
        cached = _existing_download(out_dir, video_id)
        if cached:
            print(f"[download/local] reusing cached download: {cached}", flush=True)
            return cached

    print(f"[download/local] {video_url} @ {fmt}p → {out_dir}/", flush=True)
    base_ydl_opts = {
        "outtmpl": os.path.join(out_dir, "source_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }

    # YouTube now blocks the default "web" player client with 403 Forbidden
    # (it requires a PO token). Use mobile/tv clients that still serve formats
    # without one. Configurable via YTDLP_PLAYER_CLIENTS (comma-separated).
    player_clients = [
        c.strip() for c in os.getenv(
            "YTDLP_PLAYER_CLIENTS", "android,ios,tv,web"
        ).split(",") if c.strip()
    ]
    base_ydl_opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

    # Optional escape hatch: pull cookies from a local browser to defeat
    # age/region/bot gating, e.g. YTDLP_COOKIES_FROM_BROWSER=chrome
    cookies_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    cookie_file = _cookie_file_from_env(out_dir)
    cookie_opts = {}
    if cookies_browser:
        cookie_opts["cookiesfrombrowser"] = (cookies_browser,)
    if cookie_file:
        cookie_opts["cookiefile"] = cookie_file
    has_cookies = bool(cookie_opts)

    ffmpeg_location = _ffmpeg_location()
    if ffmpeg_location:
        base_ydl_opts["ffmpeg_location"] = ffmpeg_location

    last_format_error: Optional[Exception] = None
    last_auth_error: Optional[Exception] = None
    auth_variants = []
    if cookie_opts:
        auth_variants.append(("configured cookies", cookie_opts))
    auth_variants.append(("no cookies", {}))

    try:
        path = None
        for auth_label, auth_opts in auth_variants:
            if auth_label == "no cookies" and has_cookies:
                print("[download/local] retrying without configured YouTube cookies", flush=True)

            for label, selector in _format_candidates(fmt):
                ydl_opts = dict(base_ydl_opts)
                ydl_opts.update(auth_opts)
                ydl_opts["format"] = selector
                if label == "mp4":
                    ydl_opts["merge_output_format"] = "mp4"
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        print(f"[download/local] trying format: {label} ({auth_label})", flush=True)
                        info = ydl.extract_info(video_url, download=True)
                        path = _downloaded_path(ydl, info)
                        break
                except Exception as exc:
                    message = str(exc)
                    if _is_format_unavailable_error(message):
                        last_format_error = exc
                        print(f"[download/local] format unavailable ({label}); trying fallback", flush=True)
                        continue
                    if _is_youtube_auth_error(message):
                        last_auth_error = exc
                        print(f"[download/local] YouTube auth rejected ({auth_label})", flush=True)
                        break
                    raise
            if path:
                break
            if last_auth_error:
                continue
        else:
            if last_auth_error:
                raise RuntimeError(_youtube_auth_help(has_cookies)) from last_auth_error
            if last_format_error:
                raise RuntimeError(
                    "YouTube did not provide the requested quality or any usable fallback format."
                ) from last_format_error
            raise RuntimeError("YouTube download failed before a source file was created.")

        if not path:
            if last_auth_error:
                raise RuntimeError(_youtube_auth_help(has_cookies)) from last_auth_error
            raise RuntimeError(
                "YouTube did not provide the requested quality or any usable fallback format."
            ) from last_format_error
    except Exception as exc:
        message = str(exc)
        if _is_youtube_auth_error(message):
            raise RuntimeError(_youtube_auth_help(has_cookies)) from exc
        raise

    print(f"[download/local] ready: {path}", flush=True)
    return path
