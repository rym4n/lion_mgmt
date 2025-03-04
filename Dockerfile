FROM sanicframework/sanic:24.12-py3.11

WORKDIR /app

COPY . /app

RUN pip install poetry

RUN poetry install --no-root --no-dev

ENV LION_ENV=PROD

EXPOSE 8000

CMD ["python", "server.py"]
