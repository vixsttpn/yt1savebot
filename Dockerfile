FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg curl unzip && curl -fsSL https://deno.land/install.sh | sh && mv /root/.deno/bin/deno /usr/local/bin/deno && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt && pip install --no-cache-dir -U https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz
COPY . /app
CMD ["python", "bot.py"]
