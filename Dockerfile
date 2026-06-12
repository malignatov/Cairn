FROM python:3.12-slim

WORKDIR /app

# install dependencies first for layer caching
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# markdown content — these are typically also bind-mounted by docker-compose
# so edits take effect without rebuilding the image
COPY skills ./skills
COPY schemas ./schemas
COPY guides ./guides
COPY constitution.md ./constitution.md

ENV META_DB_PATH=/app/data/meta.db \
    META_SKILLS_DIR=/app/skills \
    META_SCHEMAS_DIR=/app/schemas \
    META_GUIDES_DIR=/app/guides \
    META_CONSTITUTION_PATH=/app/constitution.md \
    META_HOST=0.0.0.0 \
    META_PORT=8000

EXPOSE 8000

CMD ["python", "-m", "meta_assistant"]
