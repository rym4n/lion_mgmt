#!/usr/bin/env python
# coding=utf-8
import ujson
from sanic import Blueprint, Request, Websocket
from sanic.log import logger
from websockets.exceptions import ConnectionClosed

from core.response import response_ok

workflow_bp = Blueprint('workflow', url_prefix='v1/workflow')
_all__ = ["workflow_bp"]


@workflow_bp.post("/new")
async def create_workflow(request):
    logger.info("Creating a new workflow")
    return response_ok("Workflow created")


#
# @workflow_bp.websocket('/run')
# async def execute_playbook(request: Request, ws: Websocket):
#     # while True:
#     #     data = "hello!"
#     #     print("Sending: " + data)
#     #     await ws.send(data)
#     #     data = await ws.recv()
#     #     print("Received: " + data)


async def chat_loop(name: str, ws):
    while True:
        try:
            data = ujson.loads(await ws.recv())
            logger.info(f"New data in {name}'s chat loop. {data}")
        except ConnectionClosed:
            logger.info(f"Chat loop with {name} closed.")
            return
        await ws.send("shit")


@workflow_bp.websocket('/run')
async def execute_action(request: Request, ws: Websocket):
    async for msg in ws:
        try:
            data = ujson.loads(msg)
            await ws.send(data.get('action', ''))
        except ConnectionClosed:
            logger.info("ws closed.")
            return
        except Exception as e:
            logger.error(e)
