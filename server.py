# coding=utf-8

from libs.sanic_worker import configure_worker_ack_timeout


configure_worker_ack_timeout()

from sanic import Sanic  # noqa: E402

from services.app_factory import create_app


app: Sanic = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
