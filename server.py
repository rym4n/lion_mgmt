# coding=utf-8

from sanic import Sanic

from libs.sanic_worker import configure_worker_ack_timeout
from services.app_factory import create_app


configure_worker_ack_timeout()
app: Sanic = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
