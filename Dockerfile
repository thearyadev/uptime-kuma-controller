FROM python:3.13.5-alpine3.21
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/
WORKDIR /app
COPY main.py .
COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --locked

ENV PROD=1
ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "main.py"]

