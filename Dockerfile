# Regina web UI — container image for demo deployments. See DEPLOY.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REGINA_WEB_HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
# Regina needs only these two; requirements.txt also carries the calendar
# agent's M365/Google clients, which the web UI never imports.
RUN pip install --no-cache-dir "anthropic>=1.6.0" "python-dotenv>=1.0.0"

COPY regina/ regina/
COPY mock_data/ mock_data/
COPY skills/ skills/
COPY web/ web/
COPY regina_web.py regina_chat.py ./

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 regina
USER regina

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=2)"

# Mode is auto: live when ANTHROPIC_API_KEY is set, otherwise mock. Force with REGINA_MODE=mock|live.
CMD ["python", "regina_web.py"]
