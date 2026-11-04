# The test harness, containerised: the same image runs locally (make e2e-docker)
# and in CI, so results do not depend on whoever's laptop ran them.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

RUN pip install uv==0.11.32

WORKDIR /app

# Dependencies first: this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY src ./src
COPY schemas ./schemas
COPY config ./config
COPY tests ./tests
# The services' pure rule functions, for the contract tests (not the services themselves).
COPY services/customs_services ./services/customs_services
RUN uv sync --frozen

# Runs as root so it can write reports to a bind-mounted ./reports on any host.
CMD ["pytest", "tests/e2e", "-v", "--junitxml=reports/junit-e2e.xml"]
