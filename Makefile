# FaceMark — common dev / ops tasks.
#
# Use:  make help

PY     := python
VENV   := .venv
PIP    := $(VENV)/bin/pip
PYBIN  := $(VENV)/bin/python
PORT   ?= 5000
HOST   ?= 0.0.0.0

.PHONY: help venv install run dev prod stop test lint clean migrate docker docker-up docker-down

help:
	@echo "FaceMark — targets"
	@echo "  make install       Install Python deps into $(VENV)"
	@echo "  make run           Run dev server (Flask) on :$(PORT)"
	@echo "  make prod          Run production server (gunicorn) on :$(PORT)"
	@echo "  make test          Run pytest"
	@echo "  make lint          Static checks (pyflakes/ruff if available)"
	@echo "  make migrate       Re-apply DB schema migrations (idempotent)"
	@echo "  make clean         Remove __pycache__, .pytest_cache, *.pyc"
	@echo "  make docker        Build the docker image"
	@echo "  make docker-up     docker compose up -d"
	@echo "  make docker-down   docker compose down"

venv:
	test -d $(VENV) || $(PY) -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run:
	$(PYBIN) app.py

dev:
	FLASK_DEBUG=1 $(PYBIN) app.py

prod:
	$(PYBIN) -m gunicorn -c gunicorn.conf.py app:app

stop:
	-pkill -f "gunicorn .* app:app" || true

test:
	$(PYBIN) -m pytest -q tests

lint:
	-$(PYBIN) -m ruff check . 2>/dev/null || $(PYBIN) -m pyflakes app.py db.py enterprise infra

migrate:
	$(PYBIN) -c "import db; db.init_db(); print('migrations applied')"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

docker:
	docker build -t facemark:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down
