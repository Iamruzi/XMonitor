FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY twitter_cli ./twitter_cli
COPY twitter_monitor ./twitter_monitor

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000

EXPOSE 8000

CMD ["twitter-monitor"]
