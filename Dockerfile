# 4yi-cad — single container: FastAPI + CadQuery + FreeCADCmd + PyVista
# (offscreen), served on $PORT.
# Built by 4yi CodeBuild -> ECR from the public repo; deployed per-consumer-org.
FROM python:3.11-slim

# System libs for VTK/PyVista offscreen rendering, OpenCASCADE (cadquery-ocp),
# and P2.0 headless FreeCAD smoke. This stays single-container for now; the
# FastAPI service invokes FreeCADCmd through the existing sandbox subprocess path.
RUN apt-get update && apt-get install -y --no-install-recommends \
      freecad \
      libgl1 \
      libgl1-mesa-dri \
      libglu1-mesa \
      libxrender1 \
      libxext6 \
      libsm6 \
      libgomp1 \
      xauth \
      xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Light web stack first (better layer caching), then the heavy CAD stack.
COPY requirements.txt requirements-cad.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r requirements-cad.txt

COPY app ./app
COPY index.html ./index.html
# TODO(eval-baseline): once evals/reports/latest.json is committed, COPY evals/reports/ so the ai_quality readiness gate can read it in-container (or set CAD_EVAL_REPORT_PATH).

# Non-root. Read-only rootfs is applied by the k8s securityContext; the sandbox
# worker writes only to TMPDIR, which must be a writable tmpfs/emptyDir mount.
RUN useradd -m -u 10001 appuser
USER appuser

ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080

# Shell form so $PORT (injected by the platform) is expanded.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
