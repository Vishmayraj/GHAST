FROM python:3.12-slim

WORKDIR /app

# CPU-only torch keeps the image far smaller than the default CUDA wheel.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.2"

COPY scoring/requirements.txt scoring/requirements-laya.txt ./scoring/
RUN pip install --no-cache-dir -r scoring/requirements.txt

# Optional Laya pattern classifier (agent/tools/pattern_classifier.py). Off by default:
# without it, or without GHAST_LAYA_MODEL, that tool is a neutral stub.
ARG WITH_LAYA=0
RUN if [ "$WITH_LAYA" = "1" ]; then pip install --no-cache-dir -r scoring/requirements-laya.txt; fi

# live_scorer.py reuses ml/ (features, model, detectors, threshold) and agent/ (state
# machine, tools, report drafter) in place rather than keeping a second copy.
COPY ml/ ./ml/
COPY agent/ ./agent/
COPY scoring/ ./scoring/

WORKDIR /app/scoring
# GHAST_CHECKPOINT and POSTGRES_DSN come from the compose environment.
CMD ["python", "live_scorer.py", "--min-votes", "2"]
