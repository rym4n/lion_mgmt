# -*- coding: utf-8 -*-
import logging
import os
import time
from xmlrpc.client import Fault, ServerProxy  # Python 3

from settings import SUPERVISOR_PATH, RY_JOB_SERVER


def write_process_config(process_name, command):
    """写入 supervisor 配置文件"""
    config_path = os.path.join(SUPERVISOR_PATH, "{}.conf".format(process_name))
    with open(config_path, "w") as f:
        f.write("""[program:{}]\ncommand={}\nautostart=true\nautorestart=true\nredirect_stderr=true\nstdout_logfile=/dev/null""".format(process_name, command))
    logging.info("写入配置文件: {}".format(config_path))


def add_process(process_name, command):
    """添加 supervisor 进程（写入配置 + reload + add）"""
    write_process_config(process_name, command)
    try:
        server = ServerProxy(RY_JOB_SERVER)
        server.supervisor.reloadConfig()
        server.supervisor.addProcessGroup(process_name)
        logging.info("进程 {} 添加成功".format(process_name))
    except Fault as e:
        if "ALREADY_ADDED" in e.faultString:
            logging.info("进程 {} 已存在".format(process_name))
        else:
            logging.info("添加进程时发生错误: {}".format(e.faultString))


def start_process(process_name):
    """启动进程（如果未运行则启动）"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        process_info = server.supervisor.getProcessInfo(process_name)
        logging.info("process_info", process_info)
        if process_info['statename'] == 'STARTING':
            logging.info("进程 {} 正在启动中".format(process_name))
            time.sleep(1)
            process_info = server.supervisor.getProcessInfo(process_name)
            if process_info['statename'] != 'RUNNING':
                server.supervisor.startProcess(process_name)
                logging.info("进程 {} 启动成功".format(process_name))
            else:
                logging.info("进程 {} 已经在运行中".format(process_name))
        elif process_info['statename'] == 'RUNNING':
            logging.info("进程 {} 已经在运行中".format(process_name))
        else:
            server.supervisor.startProcess(process_name)
            logging.info("进程 {} 启动成功".format(process_name))
    except Fault as e:
        if "BAD_NAME" in e.faultString or "UNKNOWN" in e.faultString:
            logging.info("进程 {} 不存在，无法启动".format(process_name))
        else:
            logging.info("启动进程时发生错误: {}".format(e.faultString))


def stop_process(process_name):
    """停止进程"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        server.supervisor.stopProcess(process_name)
        logging.info("进程 {} 已停止".format(process_name))
    except Fault as e:
        if "NOT_RUNNING" in e.faultString:
            logging.info("进程 {} 当前未运行".format(process_name))
        else:
            logging.info("停止进程时发生错误: {}".format(e.faultString))


def remove_process(process_name):
    """删除进程组"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        server.supervisor.removeProcessGroup(process_name)
        logging.info("进程 {} 已删除".format(process_name))
    except Fault as e:
        if "BAD_NAME" in e.faultString:
            logging.info("进程 {} 不存在，无法删除".format(process_name))
        else:
            logging.info("删除进程时发生错误: {}".format(e.faultString))


def process_status(process_name):
    """获取进程状态"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        process_info = server.supervisor.getProcessInfo(process_name)
        if process_info['statename'] == 'RUNNING':
            return "healthy"
        else:
            return "unhealthy"
    except Fault as e:
        if "BAD_NAME" in e.faultString:
            logging.info("进程 {} 不存在".format(process_name))
        else:
            logging.info("获取进程状态时发生错误: {}".format(e.faultString))
        return "unknown"
