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
    cookie_file = os.getenv("YTDLP_COOKIE_FILE", "").strip()
    if cookie_file:
        if os.path.exists(cookie_file):
            return cookie_file
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
            "Refresh the YouTube cookies and update YTDLP_COOKIE_FILE or YTDLP_COOKIES_TEXT."
        )
    return (
        "YouTube blocked this server download with a bot/sign-in check. "
        "For hosted deployments, export YouTube cookies and set YTDLP_COOKIES_TEXT "
        "or mount a cookies.txt file and set YTDLP_COOKIE_FILE. "
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
    ydl_opts = {
        "format": _format_for(fmt),
        "outtmpl": os.path.join(out_dir, "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
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
    ydl_opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

    # Optional escape hatch: pull cookies from a local browser to defeat
    # age/region/bot gating, e.g. YTDLP_COOKIES_FROM_BROWSER=chrome
    cookies_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_browser,)
    cookie_file = _cookie_file_from_env(out_dir)
    has_cookies = bool(cookies_browser or cookie_file)
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    ffmpeg_location = _ffmpeg_location()
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            path = ydl.prepare_filename(info)
            # merge_output_format may rename the extension after merge
            if not os.path.exists(path):
                stem, _ = os.path.splitext(path)
                for ext in (".mp4", ".mkv", ".webm"):
                    if os.path.exists(stem + ext):
                        path = stem + ext
                        break
    except Exception as exc:
        message = str(exc)
        if _is_youtube_auth_error(message):
            raise RuntimeError(_youtube_auth_help(has_cookies)) from exc
        raise

    print(f"[download/local] ready: {path}", flush=True)
    return path
