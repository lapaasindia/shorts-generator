"""Gunicorn settings for the hosted web app."""
import multiprocessing
import os


bind = f"0.0.0.0:{os.getenv('PORT', os.getenv('WEB_PORT', '7860'))}"

# Keep one process because jobs are tracked in memory. Threads allow multiple
# team members to browse/poll while one render job runs.
workers = int(os.getenv("WEB_WORKERS", "1"))
threads = int(os.getenv("WEB_THREADS", str(max(4, multiprocessing.cpu_count()))))
timeout = int(os.getenv("WEB_TIMEOUT_SECONDS", "3600"))
graceful_timeout = 60
accesslog = "-"
errorlog = "-"
