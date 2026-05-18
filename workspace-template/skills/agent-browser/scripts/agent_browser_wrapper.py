#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent-browser 包装脚本 - 自动处理环境变量和路径
用于 nanobot skill 集成，无需手动配置环境变量
"""

import os
import sys
import subprocess
import platform
from pathlib import Path


def get_agent_browser_path() -> Path:
    """根据系统架构获取正确的 agent-browser 可执行文件路径"""
    skill_dir = Path(__file__).parent.parent
    machine = platform.machine().lower()

    # 判断架构类型
    if machine in ("aarch64", "arm64"):
        executable_name = "agent-browser-linux-arm64"
    elif machine in ("x86_64", "amd64"):
        executable_name = "agent-browser-linux-x64"
    else:
        print(f"错误: 不支持的系统架构: {machine}", file=sys.stderr)
        sys.exit(1)

    agent_browser = skill_dir / "scripts" / executable_name

    # 检查可执行文件是否存在
    if not agent_browser.exists():
        print(f"错误: 找不到可执行文件 {agent_browser}", file=sys.stderr)
        sys.exit(1)

    # 确保 Linux 可执行文件有执行权限
    try:
        os.chmod(agent_browser, 0o755)
    except Exception as e:
        print(f"警告: 无法设置执行权限: {e}", file=sys.stderr)

    return agent_browser


def main():
    """包装脚本入口"""
    # 获取 agent-browser 可执行文件路径
    agent_browser = get_agent_browser_path()

    # 设置环境变量（自动处理 HTTPS 证书错误和 ERR_BLOCKED_BY_CLIENT）
    env = os.environ.copy()
    env["AGENT_BROWSER_IGNORE_HTTPS_ERRORS"] = "1"

    # 添加浏览器启动参数以避免 ERR_BLOCKED_BY_CLIENT 错误
    # 根据诊断报告，重点解决：HTTPS Upgrade、Private Network Access、Safe Browsing
    browser_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        "--disable-blink-features=AutomationControlled",
        # 禁用 HTTP 自动升级为 HTTPS（优先级 2）
        "--disable-features=HttpsUpgrades",
        # 禁用 Private Network Access 检查（优先级 3）
        "--disable-features=PrivateNetworkAccessSendPreflights",
        "--disable-features=PrivateNetworkAccessRespectPreflightResults",
        "--disable-features=BlockInsecurePrivateNetworkRequests",
        # 禁用 Safe Browsing 和广告过滤（优先级 5）
        "--disable-features=SafeBrowsing",
        "--disable-features=SubresourceFilter",
        "--disable-features=HeavyAdIntervention",
        "--safebrowsing-disable-auto-update",
        # 允许混合内容和不安全内容
        "--allow-running-insecure-content",
        "--disable-features=InsecureDownloadWarnings",
        # 禁用扩展和组件扩展
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-default-apps",
        "--disable-plugins",
        # 禁用代理（排除代理干扰）
        "--no-proxy-server",
        # 忽略证书错误
        "--ignore-certificate-errors",
        # 禁用 IPv6（避免连接问题）
        "--disable-ipv6",
    ]

    # 使用临时用户目录（清除 HSTS/ServiceWorker 缓存）
    import tempfile
    import time

    temp_dir = tempfile.mkdtemp(prefix=f"agent-browser-{int(time.time())}-")
    browser_args.append(f"--user-data-dir={temp_dir}")

    # 支持通过环境变量指定需要信任的不安全源
    insecure_origins = os.environ.get("AGENT_BROWSER_INSECURE_ORIGINS", "")
    if insecure_origins:
        browser_args.append(f"--unsafely-treat-insecure-origin-as-secure={insecure_origins}")

    env["AGENT_BROWSER_ARGS"] = " ".join(browser_args)

    # 如果环境变量中指定了截图目录，传递给 agent-browser
    if "AGENT_BROWSER_SCREENSHOT_DIR" in os.environ:
        env["AGENT_BROWSER_SCREENSHOT_DIR"] = os.environ["AGENT_BROWSER_SCREENSHOT_DIR"]

    # 如果没有传入参数，显示帮助信息
    if len(sys.argv) < 2:
        print("用法: python agent_browser_wrapper.py <command> [args...]")
        print("\n常用命令:")
        print("  open <url>                    - 打开网页")
        print("  screenshot [path]             - 截图")
        print("  screenshot --full [path]      - 全页截图")
        print("  fill <selector> <text>        - 填写表单")
        print("  click <selector>              - 点击元素")
        print("  snapshot                      - 获取页面结构")
        print("  wait <selector|ms>            - 等待")
        print("  close                         - 关闭浏览器")
        print("  batch --bail <cmd1> <cmd2>... - 批量执行命令")
        print("  auth save <name> --url <url> --username <user> --password <pass>")
        print("  auth login <name>             - 使用保存的认证登录")
        print("\n示例:")
        print("  python agent_browser_wrapper.py open https://example.com")
        print("  python agent_browser_wrapper.py screenshot --full screenshot.png")
        print("  python agent_browser_wrapper.py batch --bail 'open https://example.com' 'screenshot screenshot.png'")
        sys.exit(0)

    # 构造命令
    cmd = [str(agent_browser)] + sys.argv[1:]

    # 执行命令
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="ignore")

        # 输出结果
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        sys.exit(result.returncode)

    except subprocess.TimeoutExpired:
        print("错误: 命令执行超时", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
