# coding=utf-8

from sanic import Sanic

from services.app_factory import create_app

app: Sanic = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
