#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent-browser 登录辅助脚本
通用的登录并导航功能，支持自定义选择器
"""

import sys
from pathlib import Path

# 添加 scripts 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from browser_helper import login_and_navigate


def main():
    """命令行入口"""
    if len(sys.argv) < 6:
        print("用法: python browse_and_login.py <login_url> <username> <password> <target_url> <screenshot_path> [username_selector] [password_selector] [submit_selector]")
        print("\n参数说明:")
        print("  login_url         - 登录页面 URL")
        print("  username          - 用户名")
        print("  password          - 密码")
        print("  target_url        - 登录后要访问的目标页面 URL")
        print("  screenshot_path   - 截图保存路径")
        print("  username_selector - 用户名输入框选择器（可选，默认: input.el-input__inner[type='text']）")
        print("  password_selector - 密码输入框选择器（可选，默认: input.el-input__inner[type='password']）")
        print("  submit_selector   - 提交按钮选择器（可选，默认: button.el-button--primary）")
        print("\n示例:")
        print("  python browse_and_login.py https://192.168.12.254 admin Soar@2025 https://192.168.12.254/#/System/Status/Status screenshot.png")
        print("  python browse_and_login.py https://example.com admin secret https://example.com/dashboard dashboard.png 'input#username' 'input#password' 'button.login'")
        sys.exit(1)

    login_url = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    target_url = sys.argv[4]
    screenshot_path = sys.argv[5]

    # 默认选择器（适用于 Element UI）
    username_selector = sys.argv[6] if len(sys.argv) > 6 else "input.el-input__inner[type='text']"
    password_selector = sys.argv[7] if len(sys.argv) > 7 else "input.el-input__inner[type='password']"
    submit_selector = sys.argv[8] if len(sys.argv) > 8 else "button.el-button--primary"

    credentials = {
        'username_selector': username_selector,
        'password_selector': password_selector,
        'submit_selector': submit_selector,
        'username': username,
        'password': password
    }

    print(f"正在登录 {login_url}...")
    print(f"目标页面: {target_url}")

    try:
        result = login_and_navigate(
            login_url=login_url,
            target_url=target_url,
            credentials=credentials,
            screenshot_path=screenshot_path,
            extract_text=True
        )

        print("\n✓ 完成")
        print(f"截图已保存: {screenshot_path}")
        if result.get('text'):
            print(f"\n页面内容预览:\n{result['text'][:500]}...")

    except Exception as e:
        print(f"\n✗ 错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
