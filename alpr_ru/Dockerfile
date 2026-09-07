ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_VERSION=dev
ENV ALPR_RU_VERSION=${BUILD_VERSION}
ENV ALPR_RU_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache python3 py3-pip tzdata \
    && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN python -m compileall -q app

COPY run.sh /run.sh
RUN sed -i 's/\r$//' /run.sh && chmod a+x /run.sh

CMD ["/run.sh"]
