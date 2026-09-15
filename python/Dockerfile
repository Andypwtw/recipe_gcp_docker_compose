FROM python:3.12-slim

WORKDIR /workspace/python

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/workspace/python
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./

# 此版本沒有 uv.lock，因此直接由 pyproject.toml 建立環境。
# 不使用 "|| true"，避免套件安裝失敗卻讓 image 假裝 build 成功。
RUN uv sync

COPY . .

EXPOSE 5000
