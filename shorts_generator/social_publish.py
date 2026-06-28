"""Real social-platform integration: OAuth, publishing, and analytics.

Supports YouTube (Google), Instagram + Facebook (Meta Graph API).

Every network call degrades gracefully: if credentials are missing the helpers
raise ``SocialNotConfigured`` so the web layer can show an honest "setup
required" state instead of pretending a connection exists.

No heavy SDKs — only ``requests`` (already a project dependency).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

# ── Errors ─────────────────────────────────────────────────────────────────────


class SocialError(Exception):
    """Base class for social-integration problems."""


class SocialNotConfigured(SocialError):
    """Raised when the OAuth app credentials for a platform are not set."""


class SocialAuthError(SocialError):
    """Raised when a token is missing/expired and cannot be refreshed."""


# ── Provider configuration (read from environment) ─────────────────────────────

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0")

PROVIDERS = {
    "youtube": {
        "label": "YouTube",
        "client_id": lambda: os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        "client_secret": lambda: os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
    },
    # Instagram and Facebook share one Meta OAuth app.
    "instagram": {
        "label": "Instagram",
        "client_id": lambda: os.getenv("META_APP_ID", "").strip(),
        "client_secret": lambda: os.getenv("META_APP_SECRET", "").strip(),
        "auth_url": f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth",
        "token_url": f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token",
        "scopes": [
            "instagram_basic",
            "instagram_content_publish",
            "pages_show_list",
            "pages_read_engagement",
            "business_management",
        ],
    },
    "facebook": {
        "label": "Facebook",
        "client_id": lambda: os.getenv("META_APP_ID", "").strip(),
        "client_secret": lambda: os.getenv("META_APP_SECRET", "").strip(),
        "auth_url": f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth",
        "token_url": f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token",
        "scopes": [
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_posts",
            "read_insights",
        ],
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def provider_configured(platform: str) -> bool:
    """True when the OAuth client id + secret are present for ``platform``."""
    p = PROVIDERS.get(platform)
    if not p:
        return False
    return bool(p["client_id"]() and p["client_secret"]())


def configured_platforms() -> Dict[str, bool]:
    return {name: provider_configured(name) for name in PROVIDERS}


# ── OAuth: build authorize URL ─────────────────────────────────────────────────


def build_auth_url(platform: str, redirect_uri: str, state: str) -> str:
    p = PROVIDERS.get(platform)
    if not p:
        raise SocialError(f"Unknown platform: {platform}")
    if not provider_configured(platform):
        raise SocialNotConfigured(f"{p['label']} OAuth credentials are not set")

    params = {
        "client_id": p["client_id"](),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(p["scopes"]),
        "state": state,
    }
    if platform == "youtube":
        # access_type=offline + prompt=consent guarantees a refresh_token.
        params["access_type"] = "offline"
        params["prompt"] = "consent"
        params["include_granted_scopes"] = "true"
    query = "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items())
    return f"{p['auth_url']}?{query}"


# ── OAuth: exchange code → tokens ──────────────────────────────────────────────


def exchange_code(platform: str, code: str, redirect_uri: str) -> Dict:
    """Exchange an authorization code for tokens and resolve the account.

    Returns a connection dict ready to persist.
    """
    p = PROVIDERS[platform]
    if platform == "youtube":
        resp = requests.post(
            p["token_url"],
            data={
                "code": code,
                "client_id": p["client_id"](),
                "client_secret": p["client_secret"](),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        resp.raise_for_status()
        tok = resp.json()
        conn = {
            "platform": "youtube",
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token", ""),
            "expires_at": time.time() + int(tok.get("expires_in", 3600)),
            "connected_at": _utc_now(),
            "status": "connected",
        }
        _attach_youtube_channel(conn)
        return conn

    # Meta (instagram / facebook): exchange code, then upgrade to a long-lived token.
    resp = requests.get(
        p["token_url"],
        params={
            "client_id": p["client_id"](),
            "client_secret": p["client_secret"](),
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    resp.raise_for_status()
    short_tok = resp.json()["access_token"]

    long_resp = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": p["client_id"](),
            "client_secret": p["client_secret"](),
            "fb_exchange_token": short_tok,
        },
        timeout=30,
    )
    long_resp.raise_for_status()
    long_tok = long_resp.json()
    conn = {
        "platform": platform,
        "access_token": long_tok["access_token"],
        # Long-lived Meta tokens last ~60 days.
        "expires_at": time.time() + int(long_tok.get("expires_in", 60 * 24 * 3600)),
        "connected_at": _utc_now(),
        "status": "connected",
    }
    if platform == "facebook":
        _attach_facebook_page(conn)
    else:
        _attach_instagram_account(conn)
    return conn


# ── Account resolution ─────────────────────────────────────────────────────────


def _attach_youtube_channel(conn: Dict) -> None:
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {conn['access_token']}"},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if items:
        conn["channel_id"] = items[0]["id"]
        conn["handle"] = items[0]["snippet"]["title"]


def _attach_facebook_page(conn: Dict) -> None:
    r = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/me/accounts",
        params={"access_token": conn["access_token"]},
        timeout=30,
    )
    r.raise_for_status()
    pages = r.json().get("data", [])
    if pages:
        # Use the first managed page; store its page-scoped token.
        conn["page_id"] = pages[0]["id"]
        conn["page_token"] = pages[0]["access_token"]
        conn["handle"] = pages[0]["name"]


def _attach_instagram_account(conn: Dict) -> None:
    r = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/me/accounts",
        params={
            "fields": "instagram_business_account,name,access_token",
            "access_token": conn["access_token"],
        },
        timeout=30,
    )
    r.raise_for_status()
    for page in r.json().get("data", []):
        iba = page.get("instagram_business_account")
        if iba:
            conn["ig_user_id"] = iba["id"]
            conn["page_token"] = page.get("access_token", conn["access_token"])
            conn["handle"] = page.get("name", "")
            return
    raise SocialAuthError(
        "No Instagram Business account is linked to your Facebook pages. "
        "Connect an IG Business/Creator account in Meta Business settings first."
    )


# ── Token refresh ──────────────────────────────────────────────────────────────


def ensure_fresh(conn: Dict) -> Dict:
    """Refresh the access token if it is expired. Returns the (maybe updated) conn."""
    if not conn:
        raise SocialAuthError("Not connected")
    if conn.get("expires_at", 0) - 60 > time.time():
        return conn

    platform = conn["platform"]
    if platform == "youtube":
        refresh = conn.get("refresh_token")
        if not refresh:
            raise SocialAuthError("YouTube session expired; please reconnect.")
        p = PROVIDERS["youtube"]
        r = requests.post(
            p["token_url"],
            data={
                "client_id": p["client_id"](),
                "client_secret": p["client_secret"](),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        r.raise_for_status()
        tok = r.json()
        conn["access_token"] = tok["access_token"]
        conn["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
        return conn

    # Meta long-lived tokens cannot be silently refreshed past 60 days; the user
    # must reconnect. We surface that rather than failing opaquely.
    raise SocialAuthError(f"{platform} session expired; please reconnect.")


# ── Publishing ─────────────────────────────────────────────────────────────────


def publish(conn: Dict, video_path: str, title: str, caption: str,
            public_video_url: Optional[str] = None) -> Dict:
    """Publish a rendered reel to the platform in ``conn``.

    ``video_path`` is a local file (used for YouTube resumable upload).
    ``public_video_url`` is an https URL to the same file (required by Meta APIs,
    which fetch the media server-side).
    Returns ``{"id": <platform post id>, "url": <permalink>}``.
    """
    conn = ensure_fresh(conn)
    platform = conn["platform"]
    if platform == "youtube":
        return _publish_youtube(conn, video_path, title, caption)
    if platform == "facebook":
        return _publish_facebook(conn, public_video_url, caption or title)
    if platform == "instagram":
        return _publish_instagram(conn, public_video_url, caption or title)
    raise SocialError(f"Unknown platform: {platform}")


def _publish_youtube(conn: Dict, video_path: str, title: str, caption: str) -> Dict:
    if not video_path or not os.path.exists(video_path):
        raise SocialError("Rendered video file not found for upload")
    metadata = {
        "snippet": {
            "title": (title or "Short")[:100],
            "description": caption or "",
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    file_size = os.path.getsize(video_path)
    # Start resumable session.
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"part": "snippet,status", "uploadType": "resumable"},
        headers={
            "Authorization": f"Bearer {conn['access_token']}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/*",
            "X-Upload-Content-Length": str(file_size),
        },
        json=metadata,
        timeout=60,
    )
    init.raise_for_status()
    upload_url = init.headers["Location"]
    with open(video_path, "rb") as fh:
        up = requests.put(
            upload_url,
            headers={"Content-Type": "video/*", "Content-Length": str(file_size)},
            data=fh,
            timeout=600,
        )
    up.raise_for_status()
    vid = up.json()["id"]
    return {"id": vid, "url": f"https://youtube.com/watch?v={vid}"}


def _publish_facebook(conn: Dict, public_video_url: Optional[str], caption: str) -> Dict:
    if not public_video_url:
        raise SocialError("Facebook publishing needs a public https URL to the video")
    page_id = conn.get("page_id")
    page_token = conn.get("page_token")
    if not page_id or not page_token:
        raise SocialAuthError("No Facebook page available; reconnect Facebook.")
    r = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/videos",
        data={"file_url": public_video_url, "description": caption, "access_token": page_token},
        timeout=120,
    )
    r.raise_for_status()
    vid = r.json().get("id", "")
    return {"id": vid, "url": f"https://facebook.com/{vid}"}


def _publish_instagram(conn: Dict, public_video_url: Optional[str], caption: str) -> Dict:
    if not public_video_url:
        raise SocialError("Instagram publishing needs a public https URL to the video")
    ig_id = conn.get("ig_user_id")
    token = conn.get("page_token") or conn.get("access_token")
    if not ig_id:
        raise SocialAuthError("No Instagram Business account; reconnect Instagram.")
    # 1) Create a REELS media container.
    container = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_id}/media",
        data={"media_type": "REELS", "video_url": public_video_url,
              "caption": caption, "access_token": token},
        timeout=60,
    )
    container.raise_for_status()
    creation_id = container.json()["id"]
    # 2) Poll until the container is FINISHED (Meta transcodes server-side).
    for _ in range(30):
        status = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{creation_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        ).json()
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise SocialError("Instagram failed to process the video")
        time.sleep(5)
    # 3) Publish.
    pub = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=60,
    )
    pub.raise_for_status()
    media_id = pub.json()["id"]
    return {"id": media_id, "url": f"https://instagram.com/reel/{media_id}"}


# ── Analytics ──────────────────────────────────────────────────────────────────


def fetch_metrics(conn: Dict, post_external_id: str) -> Dict:
    """Pull live metrics for a previously published post.

    Returns a dict with views/likes/comments/shares (zeros if unavailable).
    """
    conn = ensure_fresh(conn)
    platform = conn["platform"]
    try:
        if platform == "youtube":
            return _metrics_youtube(conn, post_external_id)
        if platform == "facebook":
            return _metrics_facebook(conn, post_external_id)
        if platform == "instagram":
            return _metrics_instagram(conn, post_external_id)
    except requests.HTTPError:
        pass
    return {"views": 0, "likes": 0, "comments": 0, "shares": 0}


def _metrics_youtube(conn: Dict, video_id: str) -> Dict:
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics", "id": video_id},
        headers={"Authorization": f"Bearer {conn['access_token']}"},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
    s = items[0]["statistics"]
    return {
        "views": int(s.get("viewCount", 0)),
        "likes": int(s.get("likeCount", 0)),
        "comments": int(s.get("commentCount", 0)),
        "shares": 0,  # not exposed by the Data API
    }


def _metrics_instagram(conn: Dict, media_id: str) -> Dict:
    token = conn.get("page_token") or conn.get("access_token")
    r = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{media_id}/insights",
        params={"metric": "plays,likes,comments,shares", "access_token": token},
        timeout=30,
    )
    r.raise_for_status()
    out = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
    name_map = {"plays": "views", "likes": "likes", "comments": "comments", "shares": "shares"}
    for item in r.json().get("data", []):
        key = name_map.get(item.get("name"))
        if key:
            values = item.get("values", [])
            out[key] = int(values[0].get("value", 0)) if values else 0
    return out


def _metrics_facebook(conn: Dict, video_id: str) -> Dict:
    token = conn.get("page_token") or conn.get("access_token")
    r = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}",
        params={"fields": "views,likes.summary(true),comments.summary(true)", "access_token": token},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "views": int(data.get("views", 0)),
        "likes": int((data.get("likes", {}).get("summary", {}) or {}).get("total_count", 0)),
        "comments": int((data.get("comments", {}).get("summary", {}) or {}).get("total_count", 0)),
        "shares": 0,
    }


def public_summary(conn: Optional[Dict]) -> Optional[Dict]:
    """Strip secrets from a connection for safe display in the UI."""
    if not conn:
        return None
    return {
        "handle": conn.get("handle", ""),
        "status": conn.get("status", "connected"),
        "connected_at": conn.get("connected_at", ""),
        "account_id": conn.get("channel_id") or conn.get("ig_user_id") or conn.get("page_id") or "",
    }
