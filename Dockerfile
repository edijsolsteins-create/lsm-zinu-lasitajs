# Small image for a VPS / home server.  Set ACCESS_KEY (and optional API keys) as environment variables.
FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY public ./public
COPY vercel_app.py ./
ENV NO_BROWSER=1 HOST=0.0.0.0 PORT=8765 PYTHONUNBUFFERED=1
EXPOSE 8765
USER nobody
CMD ["python", "server.py"]
