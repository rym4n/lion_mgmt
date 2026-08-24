# -*- coding: utf-8 -*-
import logging
import os
import time
from xmlrpc.client import Fault, ServerProxy

from settings import RY_JOB_SERVER, SUPERVISOR_PATH


def _get_all_processes(server, process_name):
    """
    获取某个 program 的所有子进程，例如：
    my_worker → [my_worker:my_worker_00, my_worker:my_worker_01, ...]
    对于单进程，列表中就只有 [my_worker]
    """
    all_processes = []
    process_info_list = server.supervisor.getAllProcessInfo()

    for p in process_info_list:
        name = p["name"]  # 例如 my_worker, my_worker_00
        group = p["group"]  # 例如 my_worker

        # 同一个 group 下的所有 process
        if group == process_name:
            full_name = f"{group}:{name}" if ":" not in name else name
            all_processes.append(full_name)

        # 单进程 program（例：program 本身叫 my_api）
        if name == process_name:
            all_processes.append(process_name)

    return sorted(set(all_processes))


def write_process_config(process_name, command, concurrency):
    """写入 supervisor 配置文件"""
    config_path = os.path.join(SUPERVISOR_PATH, f"{process_name}.conf")
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f"""[program:{process_name}]\ncommand={command}\nnumprocs={concurrency}\nprocess_name=%(program_name)s_%(process_num)02d\nautostart=true\nautorestart=true\nredirect_stderr=true\nstdout_logfile=/dev/null\n""")
    logging.info(f"写入配置文件: {config_path}")


def add_process(process_name, command, concurrency):
    """添加 supervisor 进程（写入配置 + reload + add）"""
    write_process_config(process_name, command, concurrency)
    try:
        server = ServerProxy(RY_JOB_SERVER)
        server.supervisor.reloadConfig()
        server.supervisor.addProcessGroup(process_name)
        logging.info(f"进程 {process_name} 添加成功")
    except Fault as e:
        if "ALREADY_ADDED" in e.faultString:
            logging.info(f"进程 {process_name} 已存在")
        else:
            logging.info(f"添加进程时发生错误: {e.faultString}")


def start_process(process_name):
    """启动进程（如果未运行则启动）"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        all_processes = _get_all_processes(server, process_name)
        if not all_processes:
            logging.info(f"进程 {process_name} 不存在，无法启动")
            return False
        for p in all_processes:
            process_info = server.supervisor.getProcessInfo(p)
            logging.info(f"进程 {p} 状态: {process_info['statename']}")
            if process_info["statename"] == "STARTING":
                logging.info(f"进程 {p} 正在启动中")
                time.sleep(0.5)
                process_info = server.supervisor.getProcessInfo(p)
                if process_info["statename"] != "RUNNING":
                    server.supervisor.startProcess(p)
                    logging.info(f"进程 {p} 启动成功")
                else:
                    logging.info(f"进程 {p} 已经在运行中")
            elif process_info["statename"] == "RUNNING":
                logging.info(f"进程 {p} 已经在运行中")
            else:
                server.supervisor.startProcess(p)
                logging.info(f"进程 {p} 启动成功")
        return True
    except Fault as e:
        if "BAD_NAME" in e.faultString or "UNKNOWN" in e.faultString:
            logging.info(f"进程 {process_name} 不存在，无法启动")
            return False
        elif "ALREADY_STARTED" in e.faultString:
            return True
        else:
            logging.info(f"启动进程时发生错误: {e.faultString}")
            return False


def stop_process(process_name):
    """停止进程"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        all_processes = _get_all_processes(server, process_name)
        if not all_processes:
            logging.info(f"进程 {process_name} 不存在，无法停止")
            return False
        for p in all_processes:
            server.supervisor.stopProcess(p)
            logging.info(f"进程 {p} 已停止")
    except Fault as e:
        if "NOT_RUNNING" in e.faultString:
            logging.info(f"进程 {process_name} 当前未运行")
        else:
            logging.info(f"停止进程时发生错误: {e.faultString}")


def remove_process(process_name):
    """删除进程组"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        server.supervisor.removeProcessGroup(process_name)
        logging.info(f"进程 {process_name} 已删除")
    except Fault as e:
        if "BAD_NAME" in e.faultString:
            logging.info(f"进程 {process_name} 不存在，无法删除")
        else:
            logging.info(f"删除进程时发生错误: {e.faultString}")


def restart_process(process_name):
    """重启进程（stop + start）

    返回 False 表示该进程不由本机 supervisor 托管（未配置 RY_JOB_SERVER、
    supervisor 不可达或程序名不存在），调用方可据此降级到其他重启方式。
    """
    if not RY_JOB_SERVER:
        logging.info("未配置 RY_JOB_SERVER，跳过 supervisor 重启")
        return False
    try:
        server = ServerProxy(RY_JOB_SERVER)
        all_processes = _get_all_processes(server, process_name)
        if not all_processes:
            logging.info(f"进程 {process_name} 不存在，无法重启")
            return False
        for p in all_processes:
            try:
                server.supervisor.stopProcess(p)
            except Fault as e:
                # 本来就没在跑，直接启动即可
                if "NOT_RUNNING" not in e.faultString:
                    raise
            server.supervisor.startProcess(p)
            logging.info(f"进程 {p} 已重启")
        return True
    except Fault as e:
        if "BAD_NAME" in e.faultString or "UNKNOWN" in e.faultString:
            logging.info(f"进程 {process_name} 不存在，无法重启")
        else:
            logging.info(f"重启进程时发生错误: {e.faultString}")
        return False
    except Exception as e:
        # supervisor 不可达（连接被拒/超时等）同样按"未托管"处理，交给调用方降级
        logging.info(f"重启进程 {process_name} 失败: {e}")
        return False


def process_status(process_name):
    """获取进程状态"""
    try:
        server = ServerProxy(RY_JOB_SERVER)
        process_info = server.supervisor.getProcessInfo(process_name)
        if process_info["statename"] == "RUNNING":
            return "healthy"
        else:
            return "unhealthy"
    except Fault as e:
        if "BAD_NAME" in e.faultString:
            logging.info(f"进程 {process_name} 不存在")
        else:
            logging.info(f"获取进程状态时发生错误: {e.faultString}")
        return "unknown"
