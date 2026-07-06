FROM python:3.12-alpine
# docker-cli only — NOT the daemon. The worker is a Docker *client*; the
# daemon it talks to is the host's, through the socket compose mounts in.
RUN apk add --no-cache docker-cli
WORKDIR /app
# Workers need just requests (the pipeline is stdlib-only) — installing
# all of requirements.txt would drag fastapi/uvicorn along for nothing.
RUN pip install --no-cache-dir requests
COPY evaluator/ evaluator/
COPY worker/ worker/
CMD ["python", "worker/worker.py"]
