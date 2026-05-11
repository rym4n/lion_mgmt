#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent-browser 包装脚本 - 自动处理环境变量和路径
用于 nanobot skill 集成，无需手动配置环境变量
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    """包装脚本入口"""
    # 获取 agent-browser 可执行文件路径
    skill_dir = Path(__file__).parent.parent
    agent_browser = skill_dir / "scripts" / "agent-browser-linux-x64"

    # 检查可执行文件是否存在
    if not agent_browser.exists():
        print(f"错误: 找不到可执行文件 {agent_browser}", file=sys.stderr)
        sys.exit(1)

    # 确保 Linux 可执行文件有执行权限
    try:
        os.chmod(agent_browser, 0o755)
    except Exception as e:
        print(f"警告: 无法设置执行权限: {e}", file=sys.stderr)

    # 设置环境变量（自动处理 HTTPS 证书错误和 ERR_BLOCKED_BY_CLIENT）
    env = os.environ.copy()
    env['AGENT_BROWSER_IGNORE_HTTPS_ERRORS'] = '1'

    # 添加浏览器启动参数以避免 ERR_BLOCKED_BY_CLIENT 错误
    # 这些参数会禁用广告拦截、跨域限制等安全特性
    browser_args = [
        '--disable-web-security',
        '--disable-features=IsolateOrigins,site-per-process',
        '--disable-site-isolation-trials',
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-setuid-sandbox'
    ]
    env['AGENT_BROWSER_ARGS'] = ' '.join(browser_args)

    # 如果环境变量中指定了截图目录，传递给 agent-browser
    if 'AGENT_BROWSER_SCREENSHOT_DIR' in os.environ:
        env['AGENT_BROWSER_SCREENSHOT_DIR'] = os.environ['AGENT_BROWSER_SCREENSHOT_DIR']

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
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )

        # 输出结果
        if result.stdout:
            print(result.stdout, end='')
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)

        sys.exit(result.returncode)

    except subprocess.TimeoutExpired:
        print("错误: 命令执行超时", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
