#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent-browser 辅助函数库
提供灵活的 Python 函数接口，用于浏览器自动化操作
"""

import os
import sys
import subprocess
import platform
from pathlib import Path
from typing import Optional, Dict, List, Union

# 获取 agent-browser 可执行文件路径
SKILL_DIR = Path(__file__).parent.parent


def get_agent_browser_path() -> Path:
    """根据系统架构获取正确的 agent-browser 可执行文件路径"""
    machine = platform.machine().lower()

    # 判断架构类型
    if machine in ("aarch64", "arm64"):
        executable_name = "agent-browser-linux-arm64"
    elif machine in ("x86_64", "amd64"):
        executable_name = "agent-browser-linux-x64"
    else:
        raise RuntimeError(f"不支持的系统架构: {machine}")

    agent_browser = SKILL_DIR / "scripts" / executable_name

    # 检查可执行文件是否存在
    if not agent_browser.exists():
        raise FileNotFoundError(f"找不到可执行文件: {agent_browser}")

    # 确保可执行文件有执行权限
    try:
        os.chmod(agent_browser, 0o755)
    except Exception as e:
        print(f"警告: 无法设置执行权限: {e}", file=sys.stderr)

    return agent_browser


AGENT_BROWSER = get_agent_browser_path()


def _run_command(args: List[str], timeout: int = 30) -> str:
    """
    执行 agent-browser 命令

    Args:
        args: 命令参数列表
        timeout: 超时时间（秒）

    Returns:
        命令输出
    """
    import tempfile
    import time

    env = os.environ.copy()
    env["AGENT_BROWSER_IGNORE_HTTPS_ERRORS"] = "1"

    # 添加浏览器启动参数以避免 ERR_BLOCKED_BY_CLIENT 错误
    browser_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=HttpsUpgrades",
        "--disable-features=PrivateNetworkAccessSendPreflights",
        "--disable-features=PrivateNetworkAccessRespectPreflightResults",
        "--disable-features=BlockInsecurePrivateNetworkRequests",
        "--disable-features=SafeBrowsing",
        "--disable-features=SubresourceFilter",
        "--disable-features=HeavyAdIntervention",
        "--safebrowsing-disable-auto-update",
        "--allow-running-insecure-content",
        "--disable-features=InsecureDownloadWarnings",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-default-apps",
        "--disable-plugins",
        "--no-proxy-server",
        "--ignore-certificate-errors",
        "--disable-ipv6",
    ]

    # 使用临时用户目录
    temp_dir = tempfile.mkdtemp(prefix=f"agent-browser-{int(time.time())}-")
    browser_args.append(f"--user-data-dir={temp_dir}")

    # 支持通过环境变量指定需要信任的不安全源
    insecure_origins = os.environ.get("AGENT_BROWSER_INSECURE_ORIGINS", "")
    if insecure_origins:
        browser_args.append(f"--unsafely-treat-insecure-origin-as-secure={insecure_origins}")

    env["AGENT_BROWSER_ARGS"] = " ".join(browser_args)

    cmd = [str(AGENT_BROWSER)] + args
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=timeout)

    if result.returncode != 0 and result.stderr:
        raise RuntimeError(f"命令执行失败: {result.stderr}")

    return result.stdout


def open_page(url: str, wait_ms: int = 2000) -> str:
    """
    打开网页并等待加载

    Args:
        url: 网页 URL
        wait_ms: 等待时间（毫秒）

    Returns:
        命令输出
    """
    return batch_execute([f"open {url}", f"wait {wait_ms}"])


def take_screenshot(path: str, full_page: bool = False, annotate: bool = False) -> str:
    """
    截图

    Args:
        path: 截图保存路径
        full_page: 是否全页截图
        annotate: 是否带标注

    Returns:
        命令输出
    """
    args = ["screenshot"]
    if full_page:
        args.append("--full")
    if annotate:
        args.append("--annotate")
    args.append(path)

    return _run_command(args)


def fill_form(fields: Dict[str, str]) -> str:
    """
    填写表单

    Args:
        fields: 字段字典，格式为 {selector: value}

    Returns:
        命令输出
    """
    commands = [f"fill {selector} {value}" for selector, value in fields.items()]
    return batch_execute(commands)


def click_element(selector: str, wait_after: int = 1000) -> str:
    """
    点击元素

    Args:
        selector: CSS 选择器
        wait_after: 点击后等待时间（毫秒）

    Returns:
        命令输出
    """
    return batch_execute([f"click {selector}", f"wait {wait_after}"])


def get_page_text(selector: str = "body") -> str:
    """
    获取页面文本内容

    Args:
        selector: CSS 选择器，默认为 body

    Returns:
        页面文本内容
    """
    return _run_command(["get", "text", selector])


def get_page_html(selector: str = "body") -> str:
    """
    获取页面 HTML

    Args:
        selector: CSS 选择器，默认为 body

    Returns:
        页面 HTML
    """
    return _run_command(["get", "html", selector])


def get_snapshot(interactive_only: bool = False) -> str:
    """
    获取页面结构（可访问性树）

    Args:
        interactive_only: 是否仅返回可交互元素

    Returns:
        页面结构
    """
    args = ["snapshot"]
    if interactive_only:
        args.append("-i")
    return _run_command(args)


def wait(selector_or_ms: Union[str, int]) -> str:
    """
    等待

    Args:
        selector_or_ms: CSS 选择器或毫秒数

    Returns:
        命令输出
    """
    return _run_command(["wait", str(selector_or_ms)])


def batch_execute(commands: List[str], bail_on_error: bool = True, timeout: int = 60) -> str:
    """
    批量执行命令

    Args:
        commands: 命令列表
        bail_on_error: 遇到错误时是否停止
        timeout: 超时时间（秒）

    Returns:
        命令输出
    """
    args = ["batch"]
    if bail_on_error:
        args.append("--bail")
    args.extend(commands)

    return _run_command(args, timeout=timeout)


def close_browser(close_all: bool = False) -> str:
    """
    关闭浏览器

    Args:
        close_all: 是否关闭所有会话

    Returns:
        命令输出
    """
    args = ["close"]
    if close_all:
        args.append("--all")
    return _run_command(args)


def save_auth(name: str, url: str, username: str, password: str) -> str:
    """
    保存认证信息

    Args:
        name: 认证名称
        url: 登录页面 URL
        username: 用户名
        password: 密码

    Returns:
        命令输出
    """
    return _run_command(["auth", "save", name, "--url", url, "--username", username, "--password", password])


def login_with_auth(name: str) -> str:
    """
    使用保存的认证登录

    Args:
        name: 认证名称

    Returns:
        命令输出
    """
    return _run_command(["auth", "login", name])


def list_auth() -> str:
    """
    列出保存的认证

    Returns:
        认证列表
    """
    return _run_command(["auth", "list"])


def login_and_navigate(login_url: str, target_url: str, credentials: Dict[str, str], screenshot_path: Optional[str] = None, extract_text: bool = False) -> Dict[str, str]:
    """
    登录并导航到目标页面（常用场景）

    Args:
        login_url: 登录页面 URL
        target_url: 目标页面 URL
        credentials: 认证信息字典，包含:
            - username_selector: 用户名输入框选择器
            - password_selector: 密码输入框选择器
            - submit_selector: 提交按钮选择器
            - username: 用户名
            - password: 密码
        screenshot_path: 截图保存路径（可选）
        extract_text: 是否提取页面文本

    Returns:
        结果字典，包含 output, screenshot_path, text 等字段
    """
    # 构建命令列表
    commands = [
        f"open {login_url}",
        "wait 3000",
        f"fill {credentials['username_selector']} {credentials['username']}",
        f"fill {credentials['password_selector']} {credentials['password']}",
        f"click {credentials['submit_selector']}",
        "wait 5000",
        f"open {target_url}",
        "wait 3000",
    ]

    if screenshot_path:
        commands.append(f"screenshot --full {screenshot_path}")

    if extract_text:
        commands.append("get text body")

    # 执行命令
    output = batch_execute(commands, timeout=90)

    # 构建结果
    result = {"output": output}
    if screenshot_path:
        result["screenshot_path"] = screenshot_path
    if extract_text:
        # 提取最后的文本输出
        result["text"] = output.strip().split("\n")[-1] if output else ""

    return result


def navigate_and_extract(url: str, selectors: Dict[str, str], screenshot_path: Optional[str] = None) -> Dict[str, str]:
    """
    导航到页面并提取指定元素的内容

    Args:
        url: 页面 URL
        selectors: 选择器字典，格式为 {key: selector}
        screenshot_path: 截图保存路径（可选）

    Returns:
        提取结果字典
    """
    commands = [f"open {url}", "wait 3000"]

    if screenshot_path:
        commands.append(f"screenshot --full {screenshot_path}")

    # 添加提取命令
    for key, selector in selectors.items():
        commands.append(f"get text {selector}")

    output = batch_execute(commands)

    # 解析输出
    result = {"output": output}
    if screenshot_path:
        result["screenshot_path"] = screenshot_path

    return result
