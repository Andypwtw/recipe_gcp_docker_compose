FROM apache/airflow:2.9.2

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow
RUN pip install --no-cache-dir \
    pymysql \
    pymongo \
    kafka-python-ng \
    pandas \
    openpyxl \
    numpy \
    python-dateutil \
    requests[socks] \
    beautifulsoup4 \
    curl-cffi
