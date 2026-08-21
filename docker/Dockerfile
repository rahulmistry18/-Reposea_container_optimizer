# RepoSea pipeline — containerized Bronze -> Silver -> Gold run
FROM python:3.11-slim

WORKDIR /app

# System deps for pyarrow/openpyxl wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY data/ ./data/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: one full pipeline run (bronze -> silver -> gold -> excel -> alerts).
# For a live-fetch container, run.sh below can be swapped in as the entrypoint.
ENTRYPOINT ["python", "-m", "pipeline.run_pipeline"]
CMD ["--env", "prod"]
