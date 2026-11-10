# 新平食品 P1 · 生产镜像
# 构建：在项目根目录执行  docker build -f deploy/Dockerfile -t xinping-p1 .
# 说明：只用标准库 + openpyxl，镜像很小（约 150MB）

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    P1_ENV=prod \
    P1_DATA=/data \
    P1_HOST=0.0.0.0 \
    P1_PORT=8765 \
    P1_XLSX=/app/catalog.xlsx

WORKDIR /app

# 中文文件名在部分构建环境下会出问题，商品目录复制成英文名，用 P1_XLSX 指过去
COPY 02_商品主数据表_v1.xlsx /app/catalog.xlsx
COPY 07_p1_server.py p1_auth.py p1_schedule.py /app/
COPY p1_web /app/p1_web

RUN pip install --no-cache-dir openpyxl==3.1.5 \
 && useradd -m -u 10001 app \
 && mkdir -p /data && chown -R app:app /data /app

USER app
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=4).status==200 else 1)"

CMD ["python", "07_p1_server.py"]
