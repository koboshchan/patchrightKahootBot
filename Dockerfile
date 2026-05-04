# ── builder stage ────────────────────────────────────────────────────────────
FROM python:3.12-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install --prefix=/install -r requirements.txt

# ── runner stage ─────────────────────────────────────────────────────────────
FROM python:3.12-bookworm AS runner

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/install/lib/python3.12/site-packages

WORKDIR /app

COPY --from=builder /install /install

RUN PYTHONPATH=/install/lib/python3.12/site-packages patchright install --with-deps chromium-headless-shell

COPY . .

CMD python serve.py
