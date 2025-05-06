# -*- coding: utf-8 -*-
import logging
import os
import time
# try:
from xmlrpc.client import ServerProxy, Fault  # Python 3

from settings import SUPERVISOR_PATH

# except ImportError:
#     from xmlrpclib import ServerProxy, Fault  # Python 2

SUPERVISOR_SERVER = ServerProxy("http://10.3.0.38:9001/RPC2")
SUPERVISOR_CONFIG_DIR = SUPERVISOR_PATH


def write_process_config(process_name, command):
    """写入 supervisor 配置文件"""
    config_path = os.path.join(SUPERVISOR_CONFIG_DIR, "{}.conf".format(process_name))
    with open(config_path, "w") as f:
        f.write("""
            [program:{}]
            command={}
            autorestart=true
            """.format(process_name, command))
    logging.info("写入配置文件: {}".format(config_path))


def add_process(process_name, command):
    """添加 supervisor 进程（写入配置 + reload + add）"""
    write_process_config(process_name, command)
    try:
        SUPERVISOR_SERVER.supervisor.reloadConfig()
        SUPERVISOR_SERVER.supervisor.addProcessGroup(process_name)
        logging.info("进程 {} 添加成功".format(process_name))
    except Fault as e:
        if "ALREADY_ADDED" in e.faultString:
            logging.info("进程 {} 已存在".format(process_name))
        else:
            logging.info("添加进程时发生错误: {}".format(e.faultString))


def start_process(process_name):
    """启动进程（如果未运行则启动）"""
    try:
        process_info = SUPERVISOR_SERVER.supervisor.getProcessInfo(process_name)
        logging.info("process_info", process_info)
        if process_info['statename'] == 'STARTING':
            logging.info("进程 {} 正在启动中".format(process_name))
            time.sleep(1)
            process_info = SUPERVISOR_SERVER.supervisor.getProcessInfo(process_name)
            if process_info['statename'] != 'RUNNING':
                SUPERVISOR_SERVER.supervisor.startProcess(process_name)
                logging.info("进程 {} 启动成功".format(process_name))
            else:
                logging.info("进程 {} 已经在运行中".format(process_name))
        elif process_info['statename'] == 'RUNNING':
            logging.info("进程 {} 已经在运行中".format(process_name))
        else:
            SUPERVISOR_SERVER.supervisor.startProcess(process_name)
            logging.info("进程 {} 启动成功".format(process_name))
    except Fault as e:
        if "BAD_NAME" in e.faultString or "UNKNOWN" in e.faultString:
            logging.info("进程 {} 不存在，无法启动".format(process_name))
        else:
            logging.info("启动进程时发生错误: {}".format(e.faultString))


def stop_process(process_name):
    """停止进程"""
    try:
        SUPERVISOR_SERVER.supervisor.stopProcess(process_name)
        logging.info("进程 {} 已停止".format(process_name))
    except Fault as e:
        if "NOT_RUNNING" in e.faultString:
            logging.info("进程 {} 当前未运行".format(process_name))
        else:
            logging.info("停止进程时发生错误: {}".format(e.faultString))


def remove_process(process_name):
    """删除进程组"""
    try:
        SUPERVISOR_SERVER.supervisor.removeProcessGroup(process_name)
        logging.info("进程 {} 已删除".format(process_name))
    except Fault as e:
        if "BAD_NAME" in e.faultString:
            logging.info("进程 {} 不存在，无法删除".format(process_name))
        else:
            logging.info("删除进程时发生错误: {}".format(e.faultString))


def process_status(process_name):
    """获取进程状态"""
    try:
        process_info = SUPERVISOR_SERVER.supervisor.getProcessInfo(process_name)
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

# if __name__ == '__main__':
#     process_name = "30b21cb1-3c44-4251-93c9-40141bba647b"
#     command = "python start.py -m auto -f 剧本ID -t kafka主题"
#
#     # 示例使用
#     add_process(process_name, command)
#     start_process(process_name)
#     stop_process(process_name)
#     remove_process(process_name)
