FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY doc_workbench/ ./doc_workbench/
RUN pip install --no-cache-dir .
ENTRYPOINT ["doc-workbench"]
