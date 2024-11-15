FROM python:3.12-alpine

WORKDIR /app
COPY . .

RUN apk add --no-cache wget git
RUN wget -qO- https://astral.sh/uv/install.sh | sh
RUN /root/.local/bin/uv sync
CMD ["/root/.local/bin/uv", "run", "supernote-sync.py", "watch", "/app/data"]
