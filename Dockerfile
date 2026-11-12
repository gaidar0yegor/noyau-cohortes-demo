FROM python:3.12-slim

WORKDIR /app
COPY app/ /app/

RUN pip install --no-cache-dir pyyaml

ENV HOST=0.0.0.0 PORT=8099
EXPOSE 8099
CMD ["python", "app.py"]
