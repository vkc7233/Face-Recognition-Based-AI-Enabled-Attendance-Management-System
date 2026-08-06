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

bind            = os.environ.get('FACEMARK_BIND', '0.0.0.0:8000')
workers         = int(os.environ.get('FACEMARK_WORKERS',
                                      max(2, multiprocessing.cpu_count() * 2 + 1)))
threads         = int(os.environ.get('FACEMARK_THREADS', '4'))
worker_class    = 'gthread'
worker_tmp_dir  = '/dev/shm' if os.path.isdir('/dev/shm') else None
timeout         = int(os.environ.get('FACEMARK_TIMEOUT', '60'))
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
loglevel        = os.environ.get('FACEMARK_LOG', 'info')

# Lifecycle hooks — print a banner so ops sees the version
def on_starting(server):
    print('FaceMark gunicorn starting:', bind, 'workers=', workers,
          'threads=', threads)


def worker_int(worker):
    worker.log.info('worker %s received SIGINT', worker.pid)
