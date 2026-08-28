# coding=utf-8

from time import perf_counter


PROCESS_STARTED_AT = perf_counter()

from libs.sanic_worker import configure_worker_ack_timeout  # noqa: E402


configure_worker_ack_timeout()

from sanic import Sanic  # noqa: E402

from services.app_factory import create_app  # noqa: E402


app: Sanic = create_app(process_started_at=PROCESS_STARTED_AT)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
