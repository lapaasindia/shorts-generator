# Deploy the Team Web App

This project now includes a hosted web version for team use. The browser never
receives your Sarvam key; all transcription, clipping, and storage happen on the
server.

## What You Need

- A host that supports Docker, or a Python web service with persistent storage.
- A persistent disk or volume mounted at `/data`.
- Server-side environment variables from `.env.example`.
- HTTPS in front of the app for production traffic.

## Environment Variables

Required:

```bash
SARVAM_API_KEY=your_sarvam_key
TRANSCRIBER_PROVIDER=sarvam
LLM_PROVIDER=heuristic
WEB_AUTH_USERNAME=team
WEB_AUTH_PASSWORD=make_a_real_password
SECRET_KEY=make_a_long_random_string
DATA_DIR=/data
```

Recommended for production:

```bash
COOKIE_SECURE=true
WEB_MAX_UPLOAD_MB=2048
WEB_WORKERS=1
WEB_THREADS=4
WEB_TIMEOUT_SECONDS=3600
```

YouTube links on hosted servers can fail with `Sign in to confirm you're not a
bot` because YouTube blocks some datacenter IPs. This is not a theme/UI issue;
the server needs authenticated YouTube cookies or an API-mode backend.

Options:

```bash
# Preferred hosted setup: mount a Netscape-format cookies.txt file.
YTDLP_COOKIE_FILE=/data/youtube_cookies.txt

# Or paste the cookies.txt content into a multiline/escaped hosting secret.
YTDLP_COOKIES_TEXT="# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t..."

# Local desktop only, not hosted servers:
YTDLP_COOKIES_FROM_BROWSER=chrome

# Alternative when MUAPI_API_KEY is configured:
WEB_PIPELINE_MODE=api
```

Workspace admins can also open `/status`, paste the Netscape-format YouTube
cookies, and save them to `/data/youtube_cookies.txt`. The app will block new
YouTube URL jobs with a setup message until cookies or API mode are ready, so
failed jobs no longer fill the run log with raw `yt-dlp` trace output.

Keep `WEB_WORKERS=1` unless you replace the in-memory job tracker with Redis or
a database. Multiple threads are fine and let the team poll/download while a
render is running.

## Docker Deploy

```bash
cp .env.example .env
# Edit .env with real secrets.
docker compose up --build -d
```

Open:

```text
http://your-server:7860
```

Generated uploads, transcripts, JSON, and shorts are stored in the
`shorts_data` Docker volume.

## Generic Hosted Platform

Use the included `Dockerfile`.

Service settings:

- Build: Dockerfile
- Port: `7860`
- Health check path: `/healthz`
- Persistent disk: mount to `/data`
- Environment variables: set the values from `.env.example`

For providers with a `PORT` environment variable, the Gunicorn config will bind
to it automatically.

## Render

This repo includes `render.yaml`.

1. Push the repo to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Set the prompted secret values:
   - `SARVAM_API_KEY`
   - `WEB_AUTH_PASSWORD`
4. Confirm the persistent disk is mounted at `/data`.

Render will build the Dockerfile, expose the web service, and use `/healthz` as
the health check path.

## Railway

This repo includes `railway.json` for Dockerfile-based deployment.

1. Push the repo to GitHub.
2. Create a Railway project from the repo.
3. Add a persistent volume mounted at `/data`.
4. Set the environment variables from `.env.example`.
5. Deploy.

Railway provides a `PORT` variable automatically; `gunicorn.conf.py` uses it.

## Python-Only Deploy

On a VM or Python web service:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-web.txt
export SARVAM_API_KEY=your_sarvam_key
export TRANSCRIBER_PROVIDER=sarvam
export LLM_PROVIDER=heuristic
export WEB_AUTH_USERNAME=team
export WEB_AUTH_PASSWORD=make_a_real_password
export SECRET_KEY=make_a_long_random_string
export DATA_DIR=/data
gunicorn -c gunicorn.conf.py wsgi:app
```

## Security Notes

- Do not commit `.env`.
- Put the app behind HTTPS before setting `COOKIE_SECURE=true`.
- Use a strong `WEB_AUTH_PASSWORD`; anyone with access can consume API credits.
- Use persistent storage, or generated shorts disappear when the container is
  rebuilt.

## Job Storage

Each job writes:

- `job.json` for the job record and logs
- `result.json` for generated highlights and clip URLs
- source media, transcript cache, and rendered shorts

The app reloads `job.json` files from `WEB_OUTPUT_DIR` when it starts, so the
Recent Jobs panel keeps working after restarts as long as persistent storage is
mounted.
