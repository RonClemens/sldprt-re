# sldprt-re conversion helper (serve.py) as a service. Build context is this repo's root.
#   docker build -t sldprt-re .
#   docker run --rm -p 8765:8765 -e SLD_TOKEN=secret sldprt-re
# The helper refuses a non-loopback bind without a token, so SLD_TOKEN is mandatory here.
FROM python:3.12-slim

RUN groupadd -r app && useradd -r -g app -m app && mkdir /work && chown app:app /work
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/*.py src/sch_13006_s_t.txt ./
USER app
ENV SLD_HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=4).status==200 else 1)"

# --work is the conversion cache; mount a volume there to keep it across restarts. Extra flags (--jobs, --origin) append.
ENTRYPOINT ["python", "serve.py", "--work", "/work"]
