FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    freecad-python3 freecad-common xvfb xauth python3 python3-pyside2.qtwidgets \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONPATH=/usr/lib/freecad-python3/lib QTWEBENGINE_DISABLE_SANDBOX=1
WORKDIR /work
COPY app/techdraw_worker.py /opt/furniture/techdraw_worker.py
ENTRYPOINT ["xvfb-run", "-a", "/usr/bin/python3", "-u", "/opt/furniture/techdraw_worker.py"]
