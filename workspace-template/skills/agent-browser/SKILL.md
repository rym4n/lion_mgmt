---
name: agent-browser
description: "通用浏览器自动化工具，支持打开网页、截图、表单填写、元素交互、页面内容提取等操作"
metadata: {"nanobot":{"emoji":"🌐"}}
---

# Skill: agent-browser

通用浏览器自动化工具，专为 AI 代理设计，支持灵活的浏览器操作。

## 🎯 核心能力

- **页面导航**: 打开任意网页、等待加载
- **元素交互**: 点击、输入、选择、拖拽
- **内容提取**: 获取文本、HTML、属性值
- **截图**: 全页截图、区域截图、带标注截图（注意：- 截图自动保存到 workspace）
- **表单操作**: 填写表单、提交、验证
- **认证管理**: 保存和使用登录凭据
- **HTTPS 支持**: 自动忽略证书错误（适用于内网站点）

## 🚀 使用方式

### 方式 1: 单个命令执行

```python
import subprocess

# 打开网页
result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "open", "https://example.com"
], capture_output=True, text=True)

# 截图
result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "screenshot", "--full", "screenshot.png"
], capture_output=True, text=True)

# 获取页面文本
result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "get", "text", "body"
], capture_output=True, text=True)
```

### 方式 2: 批量命令执行（推荐）

适用于需要多步操作的场景：

```python
import subprocess

commands = [
    "open https://example.com",
    "wait 2000",
    "fill input[name='username'] admin",
    "fill input[name='password'] password123",
    "click button[type='submit']",
    "wait 3000",
    "screenshot --full result.png",
    "get text .result"
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)

print(result.stdout)
```

### 方式 3: 登录并导航（常用场景）

使用 `scripts/browse_and_login.py` 快速完成登录和导航：

```python
import subprocess

# 使用默认选择器（适用于 Element UI）
result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/browse_and_login.py",
    "https://192.168.12.254",                              # 登录页面
    "admin",                                                # 用户名
    "Soar@2025",                                           # 密码
    "https://192.168.12.254/#/System/Status/Status",       # 目标页面
    "screenshot.png"                                        # 截图路径
], capture_output=True, text=True, encoding='utf-8')

# 使用自定义选择器
result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/browse_and_login.py",
    "https://example.com/login",
    "admin",
    "password123",
    "https://example.com/dashboard",
    "dashboard.png",
    "input#username",           # 用户名选择器
    "input#password",           # 密码选择器
    "button.login-btn"          # 提交按钮选择器
], capture_output=True, text=True, encoding='utf-8')
```

### 方式 4: 使用 Python 函数（高级）

通过 `scripts/browser_helper.py` 提供的辅助函数：

```python
import sys
from pathlib import Path

# 添加 scripts 目录到 Python 路径
sys.path.insert(0, str(Path("nanobot/skills/agent-browser/scripts")))

from browser_helper import (
    open_page,
    take_screenshot,
    fill_form,
    click_element,
    get_page_text,
    batch_execute,
    login_and_navigate
)

# 简单操作
open_page("https://example.com")
take_screenshot("screenshot.png", full_page=True)
text = get_page_text("body")

# 表单填写
fill_form({
    "input[name='username']": "admin",
    "input[name='password']": "password123"
})
click_element("button[type='submit']")

# 批量操作
batch_execute([
    "open https://example.com",
    "wait 2000",
    "screenshot screenshot.png"
])

# 登录并导航（常用场景）
result = login_and_navigate(
    login_url="https://example.com/login",
    target_url="https://example.com/dashboard",
    credentials={
        "username_selector": "input[name='username']",
        "password_selector": "input[name='password']",
        "submit_selector": "button[type='submit']",
        "username": "admin",
        "password": "password123"
    },
    screenshot_path="dashboard.png",
    extract_text=True
)

print(f"截图: {result['screenshot_path']}")
print(f"页面内容: {result['text']}")
```

## 📋 可用命令

### 页面导航

```bash
# 打开网页
open <url>

# 等待（毫秒或选择器）
wait <ms>
wait <selector>
```

### 元素交互

```bash
# 填写输入框
fill <selector> <text>

# 点击元素
click <selector>

# 选择下拉框
select <selector> <value>
```

### 内容提取

```bash
# 获取文本内容
get text <selector>

# 获取 HTML
get html <selector>

# 获取属性值
get attr <selector> <attribute>

# 获取页面结构（可访问性树）
snapshot
snapshot -i  # 仅交互元素
```

### 截图

```bash
# 普通截图
screenshot [path]

# 全页截图
screenshot --full [path]

# 带标注的截图（显示可交互元素）
screenshot --annotate [path]
```

### 认证管理

```bash
# 保存认证信息
auth save <name> --url <url> --username <user> --password <pass>

# 使用保存的认证登录
auth login <name>

# 列出保存的认证
auth list

# 删除认证
auth delete <name>
```

### 会话管理

```bash
# 关闭当前浏览器
close

# 关闭所有浏览器会话
close --all
```

## 🎨 使用场景示例

### 场景 1: 简单的网页截图

```python
import subprocess

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail",
    "open https://github.com",
    "wait 3000",
    "screenshot --full github.png"
], capture_output=True, text=True)
```

### 场景 2: 登录并提取数据

```python
import subprocess

commands = [
    "open https://example.com/login",
    "wait 2000",
    "fill input#username admin",
    "fill input#password secret",
    "click button.login-btn",
    "wait 5000",
    "get text .user-info",
    "screenshot --full dashboard.png"
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)

print(result.stdout)
```

### 场景 3: 表单提交

```python
import subprocess

commands = [
    "open https://example.com/form",
    "wait 2000",
    "fill input[name='name'] John Doe",
    "fill input[name='email'] john@example.com",
    "fill textarea[name='message'] Hello World",
    "click button[type='submit']",
    "wait 3000",
    "get text .success-message"
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)
```

### 场景 4: 内网 HTTPS 站点（忽略证书错误）

```python
import subprocess

# 环境变量自动设置，无需手动配置
commands = [
    "open https://192.168.1.100",
    "wait 3000",
    "fill input.username admin",
    "fill input.password password",
    "click button.login",
    "wait 5000",
    "screenshot --full internal-site.png"
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)
```

### 场景 5: 使用保存的认证

```python
import subprocess

# 首次保存认证（只需执行一次）
subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "auth", "save", "my-site",
    "--url", "https://example.com",
    "--username", "admin",
    "--password", "secret"
])

# 之后使用
commands = [
    "open https://example.com",
    "auth login my-site",
    "wait 3000",
    "screenshot dashboard.png"
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)
```

## 🔧 选择器语法

支持标准 CSS 选择器：

```css
/* ID 选择器 */
#username

/* 类选择器 */
.login-button

/* 属性选择器 */
input[name='email']
input[type='password']

/* 组合选择器 */
form.login input[type='submit']

/* 伪类选择器 */
button:first-child
li:nth-child(2)
```

也支持 `@ref` 引用（来自 snapshot 命令）：

```bash
# 先获取页面结构
snapshot -i

# 使用 @ref 引用元素
click @e1
fill @e2 text
```

## ⚠️ 注意事项

1. **守护进程模式**: agent-browser 使用守护进程，多个命令会共享同一个浏览器会话
2. **会话隔离**: 不同的会话名称会创建独立的浏览器实例
3. **自动清理**: 使用 `close` 命令关闭浏览器，避免资源泄漏
4. **HTTPS 证书**: 自动忽略证书错误，适用于内网站点
5. **超时设置**: 默认超时 30 秒，可通过环境变量 `AGENT_BROWSER_DEFAULT_TIMEOUT` 调整
6. **截图路径**: 支持相对路径和绝对路径

## 🔍 高级功能

### 环境变量配置

包装脚本自动设置以下环境变量，也可以手动覆盖：

```python
import os
import subprocess

# 自定义配置
env = os.environ.copy()
env['AGENT_BROWSER_HEADLESS'] = '1'  # 无头模式
env['AGENT_BROWSER_SESSION_NAME'] = 'my-session'  # 会话名称
env['AGENT_BROWSER_SCREENSHOT_DIR'] = '/tmp/screenshots'  # 截图目录
env['AGENT_BROWSER_DEFAULT_TIMEOUT'] = '60000'  # 超时 60 秒

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "open", "https://example.com"
], env=env, capture_output=True, text=True)
```

### 直接调用可执行文件

如果需要更底层的控制，可以直接调用可执行文件：

```bash
# 设置权限（首次）
chmod +x nanobot/skills/agent-browser/scripts/agent-browser-linux-x64

# 设置环境变量
export AGENT_BROWSER_IGNORE_HTTPS_ERRORS=1

# 直接调用
nanobot/skills/agent-browser/scripts/agent-browser-linux-x64 open https://example.com
nanobot/skills/agent-browser/scripts/agent-browser-linux-x64 screenshot screenshot.png
```

## 📖 参考

完整命令文档：
```bash
nanobot/skills/agent-browser/scripts/agent-browser-linux-x64 --help
```
