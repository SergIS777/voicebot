FROM python:3.10-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn edge-tts
COPY app.py .
EXPOSE 8201
CMD ["python", "app.py"]
