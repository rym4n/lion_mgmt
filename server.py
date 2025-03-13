# coding=utf-8
import os

from sanic import Sanic

from services import create_app

app: Sanic = create_app()

if __name__ == '__main__':
    if os.environ.get("RY_ENV") == 'PROD':
        # 生产环境运行
        app.run(workers=os.cpu_count() * 2 + 1, host='0.0.0.0', port=8000, access_log=False)
    else:
        # 本地开发
        app.run(workers=1, host='0.0.0.0', port=8000, access_log=False)
