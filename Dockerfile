FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libssl-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# حتما Environment Variables تو Render ست بشن
# ENV API_ID=1867911
# ENV API_HASH=f9e86b274826212a2712b18754fabc47
# ENV SESSION_NAME=userbot_zip_session

CMD ["python", "main.py"]
