FROM python:3.13.7-slim

WORKDIR /app

RUN apt-get update && apt-get install -y libmagic1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ethelflow ethelflow

EXPOSE 8080
CMD ["python", "-m", "ethelflow"]