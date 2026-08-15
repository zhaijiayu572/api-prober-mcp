# API Prober MCP Server 需求与架构设计

> 状态：需求已确认，等待实现。
>
> 本文是第一版实现的约束来源。配置字段见 [配置参考](docs/configuration.md)，工具契约见 [工具参考](docs/tools.md)。

## 1. 背景

在复用缺少文档的已有 API 时，开发者通常需要打开浏览器开发者工具、完成登录、复制请求并截取响应，才能确认后台数据的字段、类型和嵌套结构。

API Prober MCP Server 为 Claude Code 和 Codex 提供受控的 HTTP 探测能力。Agent 可以读取项目代码和项目配置，识别认证方式并引导用户安全录入认证值，然后请求接口、查看响应结构，并使用客户端自身的文件工具管理项目快照。

## 2. 核心目标

- Agent 能探测接口响应格式，无需开发者手工截取网络请求。
- 全局安装一次，可被多个业务项目复用。
- 鉴权凭证不进入模型上下文、会话记录或项目快照。
- 网络访问、非只读请求和不安全传输必须具有明确的安全边界。
- 响应以数据结构为重点，限制大数组、长文本和上下文占用。
- 日志足以让 Agent 分析问题并向用户反馈，同时不记录明文秘密。
- 设计、配置、工具契约和验收标准可供后续实现及回溯。

## 3. 第一版范围

### 3.1 支持范围

- 操作系统：Linux。
- 语言：Python 3.12。
- MCP 框架：FastMCP 3.x，必要时使用底层 MCP SDK 能力。
- HTTP 客户端：httpx。
- 配置模型：Pydantic。
- MCP 传输：stdio。
- 客户端：Claude Code、Codex CLI。
- 手工鉴权注入：Bearer、自定义 Header、Cookie。
- 请求体：JSON、URL encoded form、显式 Content-Type 的 raw body。

### 3.2 非目标

- Pi Agent 集成。
- Windows 首版支持和 macOS 首版验收。
- OAuth2 授权码流程、自动刷新、多步 SSO 和扫码登录。
- 自动调用登录接口、用户名密码收集、密码加密转换和任意认证脚本。
- 验证码自动识别。
- 文件上传和 multipart 文件内容。
- MCP Server 直接写入项目快照。
- 批量探测、OpenAPI 扫描和响应 diff。
- 远程 HTTP MCP Server。

## 4. 关键原则

### 4.1 MCP 与 Agent 的职责边界

API Prober MCP 负责：

- 加载和校验会话配置。
- 网络访问授权与请求确认。
- 认证值安全录入、存储、注入、失效标记和删除。
- HTTP 请求、重定向、重试、超时和代理策略。
- 响应脱敏、数组采样、结果预算和临时缓存。
- 结构化审计日志、诊断日志和诊断查询。

Agent 负责：

- 读取项目根目录的 `.api-prober.json`。
- 调用 `configure_session` 注入项目配置。
- 根据项目代码选择接口、参数、认证类型、注入配置和敏感字段路径。
- 读取、判断、保存和更新项目内接口快照。
- 使用客户端自身的文件权限系统写项目文件。

MCP 不感知项目快照的目录、文件名、有效期或 Git 策略。

### 4.2 凭证对 Agent 不透明

- Token 和 Cookie 值不得作为 MCP 工具参数经过 Agent。
- 敏感输入通过 URL 模式 elicitation 打开的本机页面提交。
- `set_auth` 只返回状态、profile 标识和认证类型，不返回认证值。
- HTTP 请求通过 `auth_profile_name` 引用凭证，由 Server 注入。
- 已知认证值出现在响应中时必须按精确值脱敏。

### 4.3 项目配置不等于网络授权

项目仓库可能不可信。`.api-prober.json` 中声明的非默认目标必须在会话中经用户确认，不能静默扩大网络权限。

## 5. 总体架构

```text
Claude Code / Codex
        |
        | stdio MCP
        v
API Prober MCP Server
  |- tools            MCP 参数与结果适配
  |- config           全局配置、会话配置、覆盖与校验
  |- auth             敏感输入、凭证存储、注入与状态
  |- http             请求、安全策略、重定向与重试
  |- response         脱敏、采样、预算与会话缓存
  `- diagnostics      审计、诊断与查询
        |
        +--> 目标开发/测试 API
        `--> ~/.api-prober-mcp/
```

每个客户端启动独立的 stdio Server 进程。会话授权、临时 host 授权、debug 确认和响应缓存均只在该进程会话中有效。

## 6. 本地目录

所有运行配置和数据统一放在：

```text
~/.api-prober-mcp/
  config.json
  runtime/
    venv/
  credentials/
    profiles/
      <profile-id>.json
      <profile-id>.lock
  logs/
    YYYY-MM-DD/
      <session-id>.jsonl
  cache/
    responses/
      <session-id>/
```

- 根目录及子目录权限为 `0700`。
- 配置、凭证、日志和缓存文件权限为 `0600`。
- 权限过宽时，凭证存储拒绝读取并返回可操作的修复提示。
- Server 源码不存放在该目录；runtime 与用户数据相互隔离。

## 7. 配置模型

配置分为三层，越靠前优先级越高：

```text
单次工具调用参数
  -> configure_session 注入的项目配置
  -> ~/.api-prober-mcp/config.json 用户级配置
  -> Server 内置默认值和不可突破的硬限制
```

### 7.1 用户级配置

用于配置全局允许目标、并发、默认超时、结果预算、代理、日志和危险覆盖。Server 启动时读取一次；修改后重启 MCP 会话生效。

用户级配置校验失败时，Server 保持可启动以提供错误和 diagnostics，但必须拒绝鉴权及网络请求。

### 7.2 项目级配置

项目根目录使用 `.api-prober.json`。Agent 读取文件后，将解析后的对象传给 `configure_session`。Server 不扫描项目目录，也不直接读取项目文件。

项目配置必须：

- 包含 `schemaVersion: 1`。
- 通过严格 Schema 校验。
- 拒绝未知字段、错误类型和不兼容版本。
- 不包含 Token、Cookie 值或其他明文秘密。

Server 对规范化配置计算哈希。项目声明的非默认 target 在首次加载或配置哈希变化后重新确认。

### 7.3 覆盖约束

- 单次调用可以覆盖 timeout 和结果大小。
- 单次调用不能关闭安全策略、设置代理、扩大用户级危险覆盖或突破 Server 硬上限。
- 项目配置不能静默开启 debug 日志。

## 8. 网络访问控制

### 8.1 默认允许目标

默认只允许解析后仍为回环地址的：

- `localhost`
- `127.0.0.1`
- `::1`

允许 HTTP/HTTPS 和任意端口。`0.0.0.0`、局域网地址和 `.local` 域名不默认放行。

### 8.2 非默认目标确认

- 非允许目标通过 MCP elicitation 请求用户确认。
- 授权粒度是精确 origin，即 `scheme + host + port`。
- 授权只在当前 MCP 会话有效。
- 项目配置中的目标集中展示并绑定配置哈希。
- DNS 解析后的最终 IP 必须重新执行安全检查。

### 8.3 元数据服务保护

- 仅允许 `http` 和 `https` 协议。
- 已知云元数据地址和 link-local metadata 目标默认硬阻止。
- 项目配置和普通会话确认不能解除阻止。
- 用户级危险覆盖可以声明例外，但每个会话仍需再次确认。

### 8.4 HTTP 明文凭证

- 回环地址的 HTTP 默认允许。
- 非本机 HTTP 可以探测无鉴权接口。
- 每个会话首次向非本机 HTTP origin 注入认证信息前必须确认。
- `.api-prober.json` 不能永久跳过该确认。

### 8.5 TLS

- HTTPS 默认验证证书。
- 精确 origin 可以在项目配置中声明 `verify: false`。
- 每个会话首次使用未验证 TLS 时必须确认。
- 单次请求参数不能临时关闭证书验证。

### 8.6 代理

- httpx 使用 `trust_env=false`，不继承系统代理环境变量。
- 回环地址永远直连。
- 代理只能在用户级配置中显式声明。
- 第一版不支持含认证信息的代理 URL。
- 首次通过代理发送认证信息时进行会话确认。

## 9. HTTP 请求策略

### 9.1 方法确认

- `GET`、`HEAD`、`OPTIONS` 在已授权 origin 上自动执行。
- `POST`、`PUT`、`PATCH`、`DELETE` 默认进行 elicitation。
- 项目端点规则可以声明无需确认的已知查询端点。
- 授权按 `method + exact origin + path pattern` 生效，仅限当前会话。
- 不根据接口名称猜测是否安全。

### 9.2 端点匹配

- `/users/{id}` 匹配一个路径段。
- `/reports/**` 显式匹配任意后续层级。
- 不支持任意正则表达式。
- scheme、host 和 port 不支持通配符。
- 匹配前规范化重复斜杠、点路径和百分号编码。
- 多规则命中时使用最具体规则，不依赖配置顺序。

### 9.3 请求体

支持互斥的：

- `json_body`
- `form_body`
- `raw_body` + `raw_body_encoding` + `content_type`

同时支持 `query`。第一版不读取本地文件，不提供 multipart 文件上传。

- `raw_body_encoding` 支持 `utf8` 和 `base64`，默认 `utf8`。
- JSON 和 form 的 Content-Type 由 Server 生成，Agent 不能通过 Header 覆盖。

### 9.4 Header

Agent 可以设置普通业务 Header。以下 Header 由 Server 管理或禁止：

- `Host`
- `Content-Length`
- `Transfer-Encoding`
- `Connection`
- `Proxy-Authorization`
- `Proxy-Connection`
- `Upgrade`
- 标准 hop-by-hop Header
- 当前 auth profile 管理的认证 Header 和 Cookie

Header 名和值限制长度并拒绝换行符。

### 9.5 超时

优先级：

```text
单次 timeout_seconds
  -> endpointRules.timeoutSeconds
  -> 项目 defaultTimeoutSeconds
  -> 用户级默认值
  -> Server 默认 30 秒
```

- 项目可以设置 `maxTimeoutSeconds`。
- Server 不可突破的硬上限为 300 秒。
- 禁止无限超时。

### 9.6 重试

- 只读方法遇到连接中断或明确的 `502/503/504` 时最多重试一次。
- 非只读方法不自动重试。
- `429` 不自动等待或重试，只返回 `Retry-After`。
- 结果记录尝试次数。

### 9.7 并发

- 全局最多 8 个执行中请求。
- 同一 origin 最多 4 个执行中请求。
- 超出的请求排队，排队时间计入总超时并单独记录。
- 同一 auth profile 的设置和替换操作串行。

### 9.8 重定向

- 同 origin 自动跟随，最多 5 次。
- 跨 origin 时暂停并请求确认。
- 跨 origin 始终移除认证 Header 和 Cookie。
- `301/302/303` 的方法变化需要记录。
- `307/308` 保留方法和 body；非只读请求重新确认。
- 返回完整重定向链；循环或超过上限时失败。

## 10. 鉴权设计

### 10.1 auth profile 标识

认证信息绑定：

```text
projectKey + authProfileName + exact origin
```

认证值不能跨 origin 复用。跨 origin 重定向即使获准，也不能携带原 profile。

### 10.2 Agent 识别与用户录入

第一版不执行登录接口，不收集用户名或密码，也不提供认证值提取、加密转换或自定义脚本。用户通过浏览器、现有客户端或其他系统自行取得认证值，再调用 `set_auth` 在本机页面录入。

Agent 读取项目代码后负责：

- 判断项目使用 `bearer`、`header` 或 `cookie`。
- 将判断结果及其注入配置写入 `.api-prober.json`。
- 调用 `configure_session`，再调用 `set_auth(project_key, auth_profile_name)`。

用户不在页面选择认证类型。页面只读展示 Agent 配置的认证类型、目标 origin 和注入摘要，并提供一个敏感值输入框。若 Agent 判断错误，Agent 修改项目配置、重新调用 `configure_session` 和 `set_auth`，无需用户在页面理解注入字段。

### 10.3 单一认证类型

每个 auth profile 必须且只能声明一种认证类型：

- `bearer`：将用户输入作为 Token，按配置的 Header 名和 prefix 注入。
- `header`：将用户输入按配置的 Header 名和 prefix 注入。
- `cookie`：将用户输入解析为一个绑定精确 origin 的 cookie jar。

配置使用 `type` 加同名类型对象。严格 Schema 要求只出现当前类型对应的对象；同时出现多个类型对象或出现无关类型对象时返回 `CONFIG_INVALID`。由 profile 管理的认证 Header 和 Cookie 不能被 `http_request.headers` 覆盖。

### 10.4 Cookie 与 CSRF

Cookie 输入同时接受：

```text
Cookie: SESSION=abc123; XSRF-TOKEN=xyz789
SESSION=abc123; XSRF-TOKEN=xyz789
```

- 输入总大小不得超过 16 KiB，最多 50 个 Cookie。
- 拒绝 CRLF、控制字符、非法 Cookie 名和无法解析的条目。
- 手工输入的 Cookie 默认使用配置的 `defaultPath`，第一版默认 `/`。
- cookie jar 绑定 profile 的精确 origin；Cookie Domain 不能扩大该范围。
- Path 不匹配时不发送；带 `Secure` 的 Cookie 不得通过 HTTP 发送。
- 跨 origin 重定向移除全部 Cookie。
- 同 origin 响应中的 `Set-Cookie` 可以更新、轮换或删除已保存 Cookie，但不得扩大 origin 绑定。
- 日志和工具结果只记录 Cookie 名称、数量和更新动作，绝不记录值。

Cookie profile 可以从指定 Cookie 派生 CSRF Header。配置指定 `cookieName`、`headerName` 和可选的 URL decode；请求前读取当前 cookie jar 中的值并注入 Header。第一版不支持从 Cookie 之外的来源获取 CSRF 值。

派生 CSRF Header 是 Cookie 认证的附属请求完整性机制，不构成第二种认证类型，也不保存独立认证值。

### 10.5 本机敏感输入页面

- 只绑定 `127.0.0.1` 的系统随机端口。
- URL 使用高熵、单次 nonce。
- 默认 5 分钟过期。
- POST 使用 CSRF 值并校验 Origin/Host。
- 设置 CSP、`Cache-Control: no-store`、`Referrer-Policy: no-referrer`。
- 不加载外部资源。
- 提交体最大 16 KiB。
- 成功、取消或超时后关闭临时 HTTP Server。

### 10.6 凭证存储

- 每个 auth profile 使用独立 JSON 文件。
- profile ID 由绑定标识的哈希生成，不在文件名暴露项目或 host。
- 使用 `filelock` 提供跨进程锁。
- 使用同目录临时文件和原子替换。
- profile 文件权限为 `0600`。
- 认证值持久化到用户替换或显式删除。
- profile 文件记录 `auth_type` 和认证注入配置摘要，不记录项目配置全文。
- 当前配置摘要与已保存记录不一致时停止注入并标记 `invalid`，原因记为 `auth_config_changed`；用户通过 `set_auth` 原子替换后恢复。

### 10.7 状态与失效

- JWT 可以解析 `exp` 用于展示已知过期状态，但不验证签名。
- 非 JWT 不猜测过期时间。
- 项目配置通过 HTTP 状态码和 body 规则声明失效条件。
- 命中后标记 `invalid` 并停止使用，不立即删除。
- 新认证成功后原子替换旧值。
- 删除必须调用 `delete_auth_profile` 并经 elicitation 确认。

## 11. 响应处理

### 11.1 敏感数据

- Agent 通过 `sensitive_paths` 指定额外敏感位置。
- `Authorization`、`Cookie`、`Set-Cookie` 始终脱敏。
- 已保存认证值按精确值匹配并脱敏，无论响应字段名是什么。
- 脱敏保留原类型和长度信息。
- 不使用模糊的“字段名包含 token/secret”规则。

### 11.2 JSON 数组采样

- 对象数组保留字段覆盖最多的首个代表项。
- 基础类型数组保留第一项。
- 空数组保持 `[]`。
- 嵌套数组递归应用相同规则。
- 元数据记录原始长度和样本索引。

### 11.3 结果预算

`maxResultBytes` 限制整个序列化 MCP 结果，而不是只限制 body：

- 默认 20 KiB。
- 最小 4 KiB。
- 项目建议上限 100 KiB。
- Server 硬上限 1 MiB。
- envelope/元数据最多占总预算的 20%。
- 长字符串默认先截断为 256 字符并记录原长度。
- Header、脱敏路径和截断路径限制条目数，超出时记录总数。

### 11.4 大响应与 inspect

- 单响应下载硬上限默认 10 MiB。
- 规范化结果超过预算时返回 `response_id` 和省略路径。
- `inspect_response` 对 JSON 按路径和数组区间查看，对文本按字符区间查看。
- 每次 inspect 结果同样受 `maxResultBytes` 限制。

### 11.5 非 JSON

- JSON：结构化解析和路径查看。
- 文本、HTML、XML：UTF-8 文本截断和字符区间查看。
- 二进制：只返回 Content-Type、大小和 SHA-256。
- Content-Type 不可靠时依次尝试 JSON、文本和二进制，并记录实际检测类型。

### 11.6 响应 Header

默认保留结构相关 Header：

- `Content-Type`
- `Content-Length`
- `Content-Disposition`
- 标准分页 Header
- `RateLimit-*` / `X-RateLimit-*`
- 项目配置追加的 Header

`Set-Cookie` 不写入响应结果，只在 redactions 中记录其存在。运行时噪声 Header 默认省略。

### 11.7 会话缓存

- 缓存目录：`~/.api-prober-mcp/cache/responses/<session-id>/`。
- 单会话上限 100 MiB，超限删除最久未使用项。
- 文件名使用随机 `response_id`。
- 缓存保存“已脱敏但尚未按结果预算采样”的响应，不保存已知认证值。
- `set_auth` 不产生 HTTP 响应，因此不写入 response cache。
- 正常退出删除会话目录。
- 启动时清理超过 24 小时的遗留目录。
- 只能由创建缓存的会话读取。

## 12. 日志与诊断

### 12.1 日志布局

```text
~/.api-prober-mcp/logs/YYYY-MM-DD/<session-id>.jsonl
```

每个 stdio 会话写独立文件，避免跨进程写锁。

### 12.2 默认日志内容

日志覆盖：

- Server 启动、版本、客户端信息和配置哈希。
- 工具调用和 request ID。
- 配置校验与策略匹配。
- host、方法、不安全 HTTP/TLS、代理和 debug 确认结果。
- DNS、安全检查、重定向、重试、排队和超时。
- auth profile 状态变化，但不记录认证值。
- Cookie 更新日志只记录名称和 `added`、`updated`、`deleted` 动作。
- 响应状态、大小、类型、采样、截断和脱敏统计。
- 内部异常类型和本地堆栈。

默认不记录 query 值、Header 值、请求体和响应体。

### 12.3 单次 debug

- `http_request(debug=true)` 请求用户确认。
- 只对该次请求记录脱敏、裁剪后的工具输入和输出。
- 不开启长期会话状态。
- Token 和 Cookie 值永不写入日志；本机敏感输入只写入受保护的 auth profile 文件。

### 12.4 保留策略

- 默认保留 7 天。
- 总上限默认 50 MiB。
- 启动时删除超期文件，并按时间删除最旧文件满足空间限制。
- `get_diagnostics` 跨会话文件查询并按时间排序，返回结果受结果预算限制。

## 13. MCP 工具

第一版固定 7 个工具：

1. `configure_session`
2. `set_auth`
3. `get_auth_status`
4. `http_request`
5. `inspect_response`
6. `delete_auth_profile`
7. `get_diagnostics`

不提供自动登录、`set_token`、`get_token`、`login_and_save` 和 `save_snapshot`。具体参数、返回值和错误码见 [工具参考](docs/tools.md)。

## 14. 错误模型

- HTTP `4xx/5xx` 是探测结果，不作为 MCP 工具失败。
- 配置、安全、网络和内部故障使用稳定错误码。
- 错误包含 `code`、`message`、`details` 和 `next_action`。
- 不向 Agent 返回 Python 堆栈、秘密或未经处理的内部异常。
- 未预期异常返回 `INTERNAL_ERROR`，详细堆栈只写本地脱敏日志。

## 15. 安装与客户端集成

- 使用 `~/.api-prober-mcp/runtime/venv` 隔离依赖。
- `scripts/install.sh` 初始化目录并安装或升级包。
- 默认不修改客户端配置。
- `--register claude,codex` 显式调用客户端 CLI 注册。
- 已存在同名 Server 时停止，不自动覆盖。
- `scripts/uninstall.sh` 默认保留用户数据。
- `--purge-data` 才删除全部用户数据，并要求再次确认。

安装契约见 [安装指南](docs/installation.md)。

## 16. 仓库结构

```text
api-prober-mcp/
  pyproject.toml
  src/api_prober_mcp/
    server.py
    config/
    auth/
    http/
    response/
    diagnostics/
    tools/
  tests/
    unit/
    integration/
  scripts/
    install.sh
    uninstall.sh
  docs/
    configuration.md
    tools.md
    installation.md
    usage.md
  api-prober-mcp-design.md
```

### 16.1 代码约束

- 所有函数使用完整类型注解。
- 所有公开函数、类和 MCP 工具使用 Google 风格 docstring。
- 复杂安全判断说明“为什么”。
- 业务逻辑文件不超过 500 行。
- 优先纯函数和不可变模型。
- 网络、文件、日志和 elicitation 副作用集中在边界模块。
- `tools/` 只做适配，不承载业务规则。

### 16.2 质量门槛

```text
ruff check
ruff format --check
mypy
pytest
```

## 17. 测试策略

### 17.1 单元测试

- 严格配置校验和覆盖优先级。
- origin 规范化、路径模板和 metadata 阻止。
- Header 限制、脱敏和精确认证值匹配。
- 数组代表项、长值截断和结果预算。
- profile 文件锁、权限、原子替换和失效状态。
- 单一认证类型严格校验、认证配置变化阻止旧值注入。
- Cookie bundle 解析限制、origin/path/secure 规则、Set-Cookie 更新和 CSRF Header 派生。
- 错误码和日志脱敏。

### 17.2 集成测试

使用本地模拟 API 覆盖：

- JSON、form、raw 请求。
- Agent 配置认证类型后由用户手工录入认证值。
- Bearer、自定义 Header、Cookie bundle、Cookie 更新和 CSRF Header 派生。
- 401/body 失效规则。
- HTTP 风险确认、TLS 关闭确认和代理确认。
- 非只读方法确认、重定向、重试、超时和并发排队。
- 大 JSON、文本和二进制响应。
- response cache 和 diagnostics 查询。

测试不访问真实业务 API，不使用真实凭证。

### 17.3 客户端验收

自动化测试覆盖 MCP 协议层。Claude Code 和 Codex 执行人工冒烟：

- stdio Server 注册和工具发现。
- form elicitation。
- URL elicitation 和本机敏感输入页面。
- 配置注入、手工认证、请求和 inspect。
- debug 确认和 diagnostics 查询。

## 18. 验收标准

- Agent 无需得到明文 Token 即可访问受保护 API。
- Agent 能根据项目代码配置唯一认证类型，用户页面不要求选择类型。
- 认证配置变化后旧值不会按新方式注入，必须重新调用 `set_auth`。
- 默认不能访问非回环目标；经确认后只授权当前会话的精确 origin。
- 非只读请求默认确认，端点规则匹配准确且不可越权。
- 认证值不出现在工具结果、错误、日志和项目配置中。
- JSON 数组只返回一个代表项，并提供原始长度元数据。
- 默认整个工具结果不超过 20 KiB。
- 大响应可使用 `response_id` 分段检查。
- 两个并发客户端不会互相覆盖 auth profile 或日志。
- 配置未知字段和不兼容版本被明确拒绝。
- Claude Code 和 Codex 的 form/URL elicitation 冒烟通过。
- 安装、升级、卸载和数据保留行为符合文档。

## 19. 后续扩展

- Pi Agent adapter 验证与集成。
- macOS 和 Windows 正式支持。
- OAuth2、刷新 Token 和多步认证。
- OpenAPI/Swagger 批量探测。
- 由 Agent 管理的快照 diff 和变化提示。
- 系统 Keyring 凭证后端。
- 远程 MCP transport 与独立服务认证。

## 20. 一期实现取舍与风险清单

第一版以个人本地使用和核心功能交付为优先，同时保留必要的安全底线。下表中的“风险等级”表示问题不处理时的潜在影响；“一期优先级”表示建议的实现顺序：

- **P0**：核心功能或契约不可用，一期必须处理。
- **P1**：安全底线或资源边界，建议一期处理；允许采用简化实现。
- **P2**：可以在核心功能完成后处理，延期不会阻塞主流程。
- **P3**：文档和工程增强项，可放入后续版本。

| 编号 | 问题 | 风险等级 | 一期优先级 | 一期处理建议 |
|---:|---|---|---|---|
| 1 | DNS 解析结果未明确绑定到实际连接，存在 DNS rebinding 或 SSRF 绕过可能 | 严重 | P1 | 请求前检查解析出的地址，至少阻止回环、私网、link-local 和 metadata 地址；完整连接固定延后处理 |
| 2 | `projectKey + profile + origin` 不能充分证明项目身份，可能误用已有凭证 | 严重 | P1 | 个人使用阶段要求项目使用唯一 `projectKey`，首次使用已有 profile 时增加确认 |
| 3 | 压缩响应解压后可能远超 10 MiB，造成内存或 CPU 消耗 | 高 | P1 | 流式限制 wire bytes 和解压后 bytes，第一期至少支持 gzip |
| 4 | 请求 URL、Header、query、body 和配置对象缺少统一资源上限 | 高 | P1 | 增加总大小、数量和基本嵌套深度限制 |
| 5 | 重定向时 body、Header 和确认行为不够明确 | 高 | P1 | 同 origin 可自动跟随；跨 origin 停止并确认，不自动转发认证、Cookie 和 body |
| 6 | URL/path 规范化可能导致策略匹配与实际请求不一致 | 高 | P1 | 拒绝控制字符、userinfo、fragment、反斜杠和编码路径分隔符，限制 URL decode |
| 7 | Bearer/Header profile 可配置危险 Header 名 | 高 | P1 | 配置校验阶段禁止 `Host`、`Content-Length`、`Proxy-Authorization`、`Cookie` 及 hop-by-hop Header |
| 8 | Cookie jar 的 Domain、Path、数量、过期和 `Set-Cookie` 行为不完整 | 中高 | P1 | 一期只支持 host-only Cookie，限制数量和大小，处理 `Secure`、`Path` 和删除 |
| 9 | `debug=true` 可能将 query、普通 Header 或 raw body 中的秘密写入日志 | 高 | P1 | debug 默认只记录元数据、字段名、长度和脱敏结果，不记录 query 值、body 和 Header 值 |
| 10 | 异常、重定向链和 URL 可能把敏感信息写入日志 | 高 | P1 | 统一日志白名单；URL 只记录 origin/path，不直接记录异常、request 或 response 对象 |
| 11 | 本机敏感输入页面的浏览器攻击面定义不完整 | 中高 | P1 | 严格校验 Host、一次性 nonce、Content-Type 和大小，设置 `frame-ancestors 'none'` |
| 12 | 本地凭证文件的 symlink、竞态和临时文件处理未定义 | 中高 | P1 | 安全创建、拒绝 symlink、同目录临时文件、原子替换；暂缓 swap/core dump 防护 |
| 13 | `http_request.url` 的非法输入校验未与 origin 规则完全对齐 | 中 | P1 | 拒绝 userinfo、fragment、非法 scheme/端口、控制字符和空 host |
| 14 | 响应脱敏的匹配层级不明确，可能被编码形式绕过 | 高 | P1 | 解压和字符集处理后、缓存前完成脱敏；结构化 JSON 按原值匹配 |
| 15 | 结果预算可能被 metadata、redactions 或 truncations 占满 | 中 | P1 | envelope/metadata 设置固定上限，body 优先，所有结果重新序列化后确认不超预算 |
| 16 | response cache 的 ID、并发清理和异常退出行为不完整 | 中 | P2 | 一期使用随机 session/response ID、0600 文件和进程退出清理；复杂跨进程 LRU 后置 |
| 17 | GET 自动重试不一定安全，部分 GET 接口可能有副作用 | 中高 | P2 | 一期仅重试连接失败和 `502/503/504`，增加项目级 `retry: false` |
| 18 | 安装升级缺少依赖 hash、回滚和供应链校验 | 中 | P2 | 一期固定依赖版本并使用 lock 文件；完整回滚和 hash 校验后置 |
| 19 | `skipConfirmation` 的宽泛路径规则可能被滥用 | 高 | P1 | 一期限制为精确 origin、method 和 path，禁止或严格限制 `/**` |
| 20 | `inspect_response` 的 path、offset、limit 缺少充分边界 | 中 | P1 | 限制 path 长度、offset/limit、解析深度和返回结果大小 |
| 21 | JSON 解析、数组采样和长字符串可能造成资源耗尽 | 中高 | P1 | 限制 JSON 深度、数组元素数、对象键数和字符串长度 |
| 22 | `Set-Cookie` 自动更新凭证状态，可能改变后续认证行为 | 中高 | P2 | 一期保留该能力，但记录 Cookie 名称和增删改动作并设置 jar 上限 |
| 23 | 跨 origin 重定向可能泄露业务 body | 高 | P1 | 跨 origin 不自动转发 body；确认后建议重新发起请求，不自动重放 |
| 24 | metadata 阻止列表可能遗漏 IPv6 和解析后的地址变体 | 高 | P1 | 按解析后的 IP 判断，覆盖常见私网、回环、link-local 和 metadata 地址 |
| 25 | 配置变更时旧授权、profile 状态和缓存关联不明确 | 中 | P1 | 配置 hash 变化撤销临时授权并使旧 profile 失效；缓存继续只读 |
| 26 | `301/302/303` 方法转换依赖客户端默认行为 | 中 | P2 | 关闭隐式 redirect，使用明确状态机并记录每次方法变化 |
| 27 | 代理的 DNS、认证确认和回环直连规则实现复杂 | 中高 | P2 | 一期可禁用代理，或仅支持无认证 HTTP 代理 |
| 28 | 错误 details、next_action 和 diagnostics 可能绕过脱敏 | 中 | P1 | 对错误和 diagnostics 统一应用结果预算及敏感值脱敏 |

### 20.1 一期必须完成

一期至少覆盖以下功能和安全底线：

1. 严格项目配置校验、配置 hash、认证 profile 创建/替换/失效和删除。
2. Bearer、自定义 Header、Cookie + CSRF 的核心注入流程。
3. 回环地址和基本非默认 origin/IP/metadata 检查。
4. URL、Header、query、JSON/form/raw body 和配置对象的输入上限。
5. 危险 Header 禁止、控制字符拒绝和 URL 基本规范化。
6. 同 origin 重定向处理；跨 origin 不自动携带认证、Cookie 或 body。
7. 非只读方法确认和精确 endpoint rule 匹配。
8. 响应流式下载限制、至少 gzip 的解压后限制、基本 JSON 深度/数量限制。
9. 响应脱敏必须在缓存前完成；默认日志和 debug 不记录 query、body、Header 值。
10. 结果预算、数组采样、`inspect_response` 边界和错误/diagnostics 脱敏。
11. 本机敏感输入页面的 nonce、CSRF、Host/Origin 校验和基础浏览器安全 Header。
12. 凭证文件安全创建、权限校验、锁、原子替换和 symlink 拒绝。

一期 A/B 的目标是完成“配置、手工录入认证、HTTP 探测、响应查看、诊断查询”的闭环；其中安全底线允许使用本文标注的简化策略。

### 20.2 后续任务清单

以下项目从一期范围中明确拆出，作为后续任务处理，不影响一期核心闭环：

| 后续任务 | 目标 |
|---|---|
| 完整 DNS pinning | 将已检查的 DNS 地址固定到实际 TCP/TLS 连接，同时保持正确的 Host、SNI 和证书校验 |
| 更强的项目身份绑定 | 引入用户可识别的项目上下文或首次使用授权，避免仅凭字符串 `projectKey` 复用凭证 |
| 完整 Cookie RFC 兼容性 | 完善 Domain、同名不同 Path、Cookie 优先级、异常属性和大规模 `Set-Cookie` 处理 |
| 缓存并发与崩溃恢复 | 完善跨进程 LRU、锁、symlink 防护、残留目录清理和 session 绑定 |
| 幂等性和重试策略 | 支持 endpoint 级 `retry: never`/幂等声明，细分各类网络中断的重试条件 |
| 安装升级回滚 | 增加依赖 hash 校验、原子升级、失败回滚和配置迁移策略 |
| 代理增强 | 支持更完整的代理类型、DNS 行为、代理确认和代理故障诊断 |
| 更强的本地秘密保护 | 评估系统 Keyring、进程内存、swap、core dump 和备份工具泄漏风险 |
| 完整内容编码支持 | 覆盖 gzip、deflate、br 等编码的流式限制、错误处理和资源保护 |
| 更严格的响应解析沙箱 | 增加极深 JSON、异常 Unicode、超多字段和解析 CPU 时间限制 |
| 完整 redirect 状态机 | 明确所有 301/302/303/307/308 的方法、body、Header 和确认语义 |

### 20.3 一期验收补充

除第 18 节既有验收标准外，一期测试应增加：

- 解析到回环、私网、link-local、metadata 地址时被阻止。
- 跨 origin redirect 不携带认证、Cookie 或 body。
- gzip 解压后超过上限、深层 JSON 和超大请求输入被拒绝或截断。
- profile 认证 Header 配置为危险 Header 时配置失败。
- query、body、普通 Header、异常文本和 diagnostics 中不出现明文秘密。
- Cookie 的 host-only、Path、Secure、过期删除和数量上限行为正确。
- 配置 hash 变化后旧授权和旧认证值不会继续注入。
- cache 文件和 profile 文件拒绝 symlink，结果和错误始终满足预算。
