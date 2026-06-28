# Social Publishing & Analytics — Setup Guide

This app can **publish reels directly** to YouTube, Instagram, and Facebook, and
**auto-sync live analytics** (views / likes / comments) back into the Insights and
Compare tabs. To enable real connections you must create OAuth apps on each
platform and add the credentials to your `.env` file. Until you do, the Social tab
honestly shows **"Setup required"** instead of pretending to be connected.

After adding any credentials below, **restart the server**.

---

## 0. Required for everyone: a public HTTPS URL

OAuth redirects and Instagram/Facebook publishing require your server to be
reachable over **https** at a stable domain.

- For production: deploy behind https (your domain).
- For local testing: use a tunnel such as `ngrok http 7860` and use the https URL it gives you.

Add it to `.env`:

```
PUBLIC_BASE_URL=https://your-domain.com
```

> Why: Instagram & Facebook fetch the video **from your server** (they don't accept
> file uploads), and all three platforms redirect the browser back to
> `PUBLIC_BASE_URL/oauth/<platform>/callback` after sign-in. YouTube uploads the
> file directly, so YouTube-only use can work on `http://127.0.0.1:7860`.

---

## 1. YouTube (Google)

1. Go to **Google Cloud Console** → create a project.
2. Enable **YouTube Data API v3** (and *YouTube Analytics API* for richer stats).
3. **APIs & Services → OAuth consent screen**: set External, add your account as a
   test user, and add the scopes:
   - `.../auth/youtube.upload`
   - `.../auth/youtube.readonly`
   - `.../auth/yt-analytics.readonly`
4. **Credentials → Create OAuth client ID → Web application**. Add this authorized
   redirect URI:
   ```
   https://your-domain.com/oauth/youtube/callback
   ```
5. Copy the client ID and secret into `.env`:
   ```
   GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=xxxx
   ```

> Google verification: while your app is in "Testing" mode only added test users
> can sign in (fine for your own channel). Publishing the consent screen for other
> users requires Google's review.

---

## 2. Instagram + Facebook (Meta — one app for both)

Instagram publishing **requires an Instagram Business or Creator account linked to
a Facebook Page.** Personal IG accounts cannot publish via the API.

1. Go to **developers.facebook.com** → My Apps → Create App → type **Business**.
2. Add products: **Facebook Login** and the **Instagram Graph API**.
3. In **Facebook Login → Settings**, add the valid OAuth redirect URIs:
   ```
   https://your-domain.com/oauth/instagram/callback
   https://your-domain.com/oauth/facebook/callback
   ```
4. Under **App settings → Basic**, copy the App ID and App Secret into `.env`:
   ```
   META_APP_ID=xxxx
   META_APP_SECRET=xxxx
   ```
5. Make sure your IG Business account is linked to a Facebook Page you manage
   (Meta Business Suite → Settings → linked accounts).

> App Review: to publish for accounts other than your own test users, Meta
> requires App Review for `instagram_content_publish`, `pages_manage_posts`, etc.
> For your own connected accounts in Development mode it works without review.

---

## 3. Full `.env` block

```
# Public URL (required for IG/FB + all OAuth redirects)
PUBLIC_BASE_URL=https://your-domain.com

# YouTube (Google)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Instagram + Facebook (Meta)
META_APP_ID=
META_APP_SECRET=
META_GRAPH_VERSION=v21.0
```

---

## 4. How it works once configured

| Step | What happens |
|------|--------------|
| **Connect** | Social tab → Connect → real OAuth consent → tokens stored server-side in `social_connections.json` (treat as secret; it's git-ignored). |
| **Schedule** | Calendar → "+ Schedule reel" → pick a render, platforms, and a date/time. Stored in `scheduled_posts.json`. |
| **Auto-publish** | A background worker checks every 60s and publishes due reels: YouTube via resumable upload; IG/FB via the Graph API (fetching the public video URL). |
| **Track** | Published posts are added to Insights automatically with their platform post ID. |
| **Sync analytics** | Insights → "⟳ Sync analytics" pulls live views/likes/comments and re-runs the viral/underperforming analysis, which flows into Compare. |

## 5. Security notes

- `social_connections.json` and `scheduled_posts.json` hold access tokens and are
  **git-ignored** — never commit them.
- Tokens are refreshed automatically for YouTube. Meta long-lived tokens last ~60
  days; after that the app asks you to reconnect.
