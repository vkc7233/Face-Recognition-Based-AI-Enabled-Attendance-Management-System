"""
Production gunicorn config for FaceMark.

Run with:  gunicorn -c gunicorn.conf.py app:app

Notes
-----
* Worker class is sync+threads — keeps OpenCV stable and gives us concurrency
  without async refactor.
* Auto-tune workers to 2 × CPU + 1 unless overridden via env.
* Pre-fork loads the recogniser + DB once, then forks — meaningful startup
  speedup at scale.
* Access log goes to stdout in JSON format (one line per request) so it
  drops straight into Loki / Datadog / Splunk.
"""

import multiprocessing
import os


def _env_str(name: str, default: str) -> str:
    """Read an env var, treating set-but-empty as absent.

    os.environ.get(name, default) only falls back when the key is *missing*.
    Dashboards (Render, Heroku, ECS) commonly inject a key with an empty value
    when the field is left blank, which yields '' and defeats the default.
    """
    return (os.environ.get(name) or '').strip() or default


def _env_int(name: str, default: int) -> int:
    """Same as _env_str, but for ints — never raises on blank/garbage input.

    A blank FACEMARK_WORKERS used to crash the whole config file with
    "ValueError: invalid literal for int() with base 10: ''", which gunicorn
    reports as "Failed to read config file" and exits before binding a port.
    """
    raw = _env_str(name, '')
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# Bind: prefer the platform's $PORT (Render/Heroku assign it) over the default.
_port           = _env_str('PORT', '8000')
bind            = _env_str('FACEMARK_BIND', f'0.0.0.0:{_port}')

# Workers: honour $WEB_CONCURRENCY (what PaaS platforms set to match the
# instance size) before falling back to the CPU heuristic. cpu_count() reports
# the *host* CPUs, not the container's share, so the heuristic over-forks and
# OOMs on small instances.
workers         = _env_int('FACEMARK_WORKERS',
                           _env_int('WEB_CONCURRENCY',
                                    min(8, max(2, multiprocessing.cpu_count() * 2 + 1))))
threads         = _env_int('FACEMARK_THREADS', 4)
worker_class    = 'gthread'
worker_tmp_dir  = '/dev/shm' if os.path.isdir('/dev/shm') else None
timeout         = _env_int('FACEMARK_TIMEOUT', 60)
graceful_timeout = 30
keepalive       = 5

preload_app     = True

# Logging: JSON access log; error log to stderr
accesslog       = '-'
errorlog        = '-'
access_log_format = (
    '{"ts":"%(t)s","ip":"%(h)s","method":"%(m)s","path":"%(U)s",'
    '"status":%(s)s,"len":%(b)s,"ms":%(D)s,"ua":"%(a)s"}'
)
loglevel        = _env_str('FACEMARK_LOG', 'info')

# Lifecycle hooks — print a banner so ops sees the version
def on_starting(server):
    print('FaceMark gunicorn starting:', bind, 'workers=', workers,
          'threads=', threads)


def worker_int(worker):
    worker.log.info('worker %s received SIGINT', worker.pid)
