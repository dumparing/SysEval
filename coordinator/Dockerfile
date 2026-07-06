# Coordinator image. Alpine: small, and pure-Python deps (plus pydantic's
# prebuilt musl wheels) mean nothing needs compiling.
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY coordinator/ coordinator/
# 0.0.0.0, not localhost: inside a container, localhost is the container.
# Binding 0.0.0.0 is what lets compose's network (and your host) reach it.
CMD ["uvicorn", "coordinator.server:app", "--host", "0.0.0.0", "--port", "8000"]
