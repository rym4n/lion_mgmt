# 工具使用说明

工具签名通过函数调用自动提供。
此文件记录非显而易见的约束和使用模式。

## AIOE 平台能力路由

- `lion-mgmt` skill 用于 AIOE 后台数据查询和操作，例如查询 IOC、列出剧本/动作、查看或创建作战室、执行剧本或动作。
- 调用 `lion-mgmt` skill 时，如果当前消息上下文包含 `sender_name`，必须使用它作为登录认证用户名；没有提供时再使用 `admin`。
- 纯知识问答、寒暄和概念解释不要调用 `lion-mgmt` skill。
- 需要后台真实数据时必须调用 skill，不要凭记忆或模板答案回复。

## 自动化任务能力

- 浏览器自动化：网页操作、数据抓取、表单填写、截图取证等。
- SSH 服务器操作：远程命令执行、文件管理、系统配置等。
- API 调用：接口测试、数据查询、文档生成等。
- 必须提供真实执行结果（截图、命令输出、接口返回等），禁止伪造；截图必须是真实截图，即使拥有真实数据也不能自己生成图片当作截图。
- 用户要求“生成代码”时，回顾会话中的操作历史，必须使用 `aioe-app-dev` skill 生成，并返回 JSON 格式：`{"config": "...", "main": "..."}`。

## 安全运维场景的工具使用建议

### 日志分析
- 使用 `grep` 搜索日志中的异常模式（如失败登录、错误代码）
- 使用 `exec` 执行 `awk`/`sed` 进行复杂日志解析
- 大文件使用 `head_limit` 和 `offset` 分页处理

### 系统排查
- 使用 `exec` 执行诊断命令（`ps`/`netstat`/`ss`/`lsof`）
- 使用 `glob` 查找配置文件、日志文件、可疑文件
- 使用 `read_file` 读取配置和日志内容

### 威胁检测
- 使用 `grep` 搜索 IOC（IP地址、域名、文件哈希）
- 使用 `exec` 执行安全工具（`nmap`/`tcpdump`/`strings`）
- 使用 `glob` 查找可疑文件（webshell特征、异常扩展名）

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## glob — File Discovery

- Use `glob` to find files by pattern before falling back to shell commands
- Simple patterns like `*.py` match recursively by filename
- Use `entry_type="dirs"` when you need matching directories instead of files
- Use `head_limit` and `offset` to page through large result sets
- Prefer this over `exec` when you only need file paths

## grep — Content Search

- Use `grep` to search file contents inside the workspace
- Default behavior returns only matching file paths (`output_mode="files_with_matches"`)
- Supports optional `glob` filtering plus `context_before` / `context_after`
- Supports `type="py"`, `type="ts"`, `type="md"` and similar shorthand filters
- Use `fixed_strings=true` for literal keywords containing regex characters
- Use `output_mode="files_with_matches"` to get only matching file paths
- Use `output_mode="count"` to size a search before reading full matches
- Use `head_limit` and `offset` to page across results
- Prefer this over `exec` for code and history searches
- Binary or oversized files may be skipped to keep results readable

## cron — Scheduled Reminders

- Please refer to cron skill for usage.
