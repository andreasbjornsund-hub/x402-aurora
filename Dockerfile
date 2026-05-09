FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv pip install --system "fastapi>=0.115" "uvicorn>=0.34" "x402[fastapi,evm]>=2.8,<2.9" "python-dotenv>=1.0" "httpx>=0.27" "PyJWT[crypto]>=2.8"

COPY main.py cdp_auth.py cities.py met_client.py parsers.py ./
COPY static static

ENV FACILITATOR_URL=https://x402.org/facilitator
# NOTE: EVM_ADDRESS and any agent-specific secrets should be set via
# `flyctl secrets set` rather than baked into the image.

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "60"]
