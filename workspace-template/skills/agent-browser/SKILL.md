---
name: agent-browser
description: "浏览器自动化工具。重要：1) 禁止使用 playwright/selenium 库，必须使用本 skill；2) 每次浏览器操作后必须执行 screenshot --full 命令获取截图；3) 截图自动保存到 workspace 并发送给用户。"
metadata: {"nanobot":{"emoji":"🌐"}}
---

# Skill: agent-browser

浏览器自动化工具，专为 AI 代理设计。

## ⚠️ 重要规则

1. **禁止直接使用浏览器库**：不要使用 playwright、selenium、puppeteer 等库
2. **必须使用本 skill**：所有浏览器操作必须通过本 skill 执行
3. **必须执行截图命令**：每次浏览器操作后必须在 commands 列表末尾添加 `screenshot --full result.png`
4. **截图自动发送**：截图保存到 workspace 后会自动发送给用户
5. **私有网络场景优化**：本 skill 主要用于私有网络环境（内网、测试环境），已自动禁用所有证书验证和安全检查。调用时无需考虑 HTTPS 证书、自签名证书、证书过期等问题，工具会自动处理
6. **自动处理安全限制**：已自动配置参数避免 ERR_BLOCKED_BY_CLIENT 等错误

## 🎯 核心能力

- **页面导航**: 打开网页、等待加载
- **元素交互**: 点击、输入、选择
- **内容提取**: 获取文本、HTML、属性值
- **截图**: 全页/区域/带标注截图（必须执行）
- **表单操作**: 填写表单、提交
- **认证管理**: 保存和使用登录凭据
- **HTTPS 支持**: 自动忽略证书错误（适用于内网站点）

## 🔄 标准工作流程

1. **接收任务**: 获取目标 URL、操作步骤、认证信息
2. **执行操作**: 使用 batch 模式执行浏览器自动化命令
3. **截图保存**: 必须执行 screenshot --full 命令，截图自动保存到 workspace 目录

## 🚀 使用方式

### 方式 1: 批量命令执行（推荐）

**注意：commands 列表末尾必须包含 screenshot --full 命令**

```python
import subprocess

commands = [
    "open https://example.com",
    "wait 3000",
    "screenshot --full result.png"  # 必须包含此命令
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)
```

### 完整示例（带登录）

```python
import subprocess

commands = [
    "open https://example.com/login",
    "wait 2000",
    "fill input[name='username'] admin",
    "fill input[name='password'] password123",
    "click button[type='submit']",
    "wait 3000",
    "screenshot --full result.png"  # 必须包含此命令
]

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)
```

### 方式 2: 登录并导航

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
    "https://example.com/login", "admin", "password123",
    "https://example.com/dashboard", "dashboard.png",
    "input#username", "input#password", "button.login-btn"
], capture_output=True, text=True, encoding='utf-8')
```

### 方式 3: Python 函数（高级）

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("nanobot/skills/agent-browser/scripts")))

from browser_helper import (
    open_page, take_screenshot, fill_form,
    click_element, get_page_text, batch_execute, login_and_navigate
)

# 简单操作
open_page("https://example.com")
take_screenshot("screenshot.png", full_page=True)
text = get_page_text("body")

# 登录并导航
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
```

## 📋 可用命令

### 页面导航
```bash
open <url>                    # 打开网页
wait <ms>                     # 等待毫秒
wait <selector>               # 等待元素出现
```

### 元素交互
```bash
fill <selector> <text>        # 填写输入框
click <selector>              # 点击元素
select <selector> <value>     # 选择下拉框
```

### 内容提取
```bash
get text <selector>           # 获取文本
get html <selector>           # 获取 HTML
get attr <selector> <attr>    # 获取属性值
snapshot                      # 获取页面结构
snapshot -i                   # 仅交互元素
```

### 截图
```bash
screenshot [path]             # 普通截图
screenshot --full [path]      # 全页截图
screenshot --annotate [path]  # 带标注截图
```

### 认证管理
```bash
auth save <name> --url <url> --username <user> --password <pass>
auth login <name>
auth list
auth delete <name>
```

### 会话管理
```bash
close                         # 关闭当前浏览器
close --all                   # 关闭所有会话
```

## 🎨 使用场景

### 场景 1: 网页截图
```python
commands = [
    "open https://github.com",
    "wait 3000",
    "screenshot --full github.png"
]
result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail"
] + commands, capture_output=True, text=True)
```

### 场景 2: 登录并提取数据
```python
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
```

### 场景 3: 内网 HTTPS 站点
```python
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

### 场景 4: 使用保存的认证
```python
# 首次保存认证
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
#username                     /* ID 选择器 */
.login-button                 /* 类选择器 */
input[name='email']           /* 属性选择器 */
form.login input[type='submit']  /* 组合选择器 */
button:first-child            /* 伪类选择器 */
```

支持 `@ref` 引用（来自 snapshot 命令）：
```bash
snapshot -i                   # 获取页面结构
click @e1                     # 使用 @ref 引用元素
fill @e2 text
```

## ⚠️ 注意事项

1. **守护进程模式**: 多个命令共享同一浏览器会话
2. **会话隔离**: 不同会话名称创建独立浏览器实例
3. **自动清理**: 使用 `close` 命令关闭浏览器，避免资源泄漏
4. **HTTPS 证书**: 自动忽略证书错误，适用于内网站点
5. **超时设置**: 默认 30 秒，可通过 `AGENT_BROWSER_DEFAULT_TIMEOUT` 调整
6. **截图路径**: 支持相对路径和绝对路径
7. **ERR_BLOCKED_BY_CLIENT 自动修复**: 已自动配置以下参数避免此错误：
   - 禁用 HTTPS 自动升级（HttpsUpgrades）
   - 禁用私有网络访问检查（Private Network Access）
   - 禁用 Safe Browsing 和广告过滤
   - 使用临时用户目录（清除 HSTS/ServiceWorker 缓存）
   - 禁用代理和 IPv6
8. **内网 HTTP 站点**: 对于内网 HTTP 站点（如 http://paas.hongcloud.net），可通过环境变量 `AGENT_BROWSER_INSECURE_ORIGINS` 指定信任的源

## 🔍 高级功能

### 环境变量配置
```python
import os, subprocess

env = os.environ.copy()
env['AGENT_BROWSER_HEADLESS'] = '1'  # 无头模式
env['AGENT_BROWSER_SESSION_NAME'] = 'my-session'
env['AGENT_BROWSER_SCREENSHOT_DIR'] = '/tmp/screenshots'
env['AGENT_BROWSER_DEFAULT_TIMEOUT'] = '60000'
# 信任特定的不安全源（用于内网 HTTP 站点）
env['AGENT_BROWSER_INSECURE_ORIGINS'] = 'http://paas.hongcloud.net,http://internal.example.com'

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "open", "https://example.com"
], env=env, capture_output=True, text=True)
```

### 直接调用可执行文件
```bash
# 设置权限（首次）
chmod +x nanobot/skills/agent-browser/scripts/agent-browser-linux-x64

# 设置环境变量并调用
export AGENT_BROWSER_IGNORE_HTTPS_ERRORS=1
nanobot/skills/agent-browser/scripts/agent-browser-linux-x64 open https://example.com
nanobot/skills/agent-browser/scripts/agent-browser-linux-x64 screenshot screenshot.png
```

## 📖 参考

完整命令文档：
```bash
nanobot/skills/agent-browser/scripts/agent-browser-linux-x64 --help
```

## 🔧 故障排查

### ERR_BLOCKED_BY_CLIENT 错误

如果遇到 `net::ERR_BLOCKED_BY_CLIENT` 错误，本 skill 已自动配置以下修复措施：

**自动修复项**：
1. ✅ 禁用 HTTPS 自动升级（`HttpsUpgrades`）
2. ✅ 禁用私有网络访问检查（`PrivateNetworkAccessSendPreflights`）
3. ✅ 禁用 Safe Browsing 和广告过滤
4. ✅ 使用临时用户目录（清除 HSTS/ServiceWorker 缓存）
5. ✅ 禁用所有扩展和组件扩展
6. ✅ 禁用代理和 IPv6

**如果问题仍然存在**，可能的原因和解决方案：

#### 原因 1：agent-browser 内部的请求拦截
如果 agent-browser 工具内部使用了 `route.abort()` 拦截特定请求，需要检查工具源码。

**诊断方法**：
```python
# 在命令中添加详细日志
commands = [
    "open http://your-site.com",
    "wait 5000",
    "get text body",  # 尝试获取页面内容
    "screenshot --full debug.png"
]
```

如果 `get text body` 返回空或错误，说明页面加载失败。

#### 原因 2：特定域名需要信任
对于内网 HTTP 站点，使用环境变量指定信任的源：

```python
import os, subprocess

env = os.environ.copy()
env['AGENT_BROWSER_INSECURE_ORIGINS'] = 'http://paas.hongcloud.net'

result = subprocess.run([
    "python", "nanobot/skills/agent-browser/scripts/agent_browser_wrapper.py",
    "batch", "--bail",
    "open http://paas.hongcloud.net",
    "wait 5000",
    "screenshot --full result.png"
], env=env, capture_output=True, text=True)
```

#### 原因 3：DNS 解析问题
如果域名解析有问题，可以直接使用 IP 地址：

```python
# 先解析域名
import socket
ip = socket.gethostbyname('paas.hongcloud.net')
print(f"Resolved IP: {ip}")

# 使用 IP 访问
commands = [f"open http://{ip}", "wait 5000", "screenshot --full result.png"]
```

#### 原因 4：需要开启网络日志
如果以上都无法解决，开启 Chromium NetLog 进行深度诊断：

```python
env = os.environ.copy()
# 在现有参数基础上添加 NetLog
env['AGENT_BROWSER_ARGS'] = env.get('AGENT_BROWSER_ARGS', '') + ' --log-net-log=/tmp/netlog.json --net-log-capture-mode=Everything'
```

然后访问 https://netlog-viewer.appspot.com/ 上传 `/tmp/netlog.json` 分析详细错误。

### 其他常见问题

**问题：截图为空白页**
- 原因：页面加载时间不足
- 解决：增加 `wait` 时间，或使用 `wait <selector>` 等待特定元素

**问题：无法找到元素**
- 原因：选择器错误或页面结构变化
- 解决：使用 `snapshot -i` 查看页面结构，确认正确的选择器

**问题：浏览器进程残留**
- 原因：未正确关闭浏览器
- 解决：在命令末尾添加 `close` 命令，或使用 `close --all` 清理所有会话
