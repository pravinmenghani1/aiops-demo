FROM python:3.12-slim
WORKDIR /app
COPY svc.py /app/svc.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/svc.py"]
