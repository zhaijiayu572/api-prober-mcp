# 使用 API Prober 探测接口

> 状态：Tutorial/How-to 实现契约。工具将在代码实现后可用。

本文面向使用 Claude Code 或 Codex 的开发人员，演示从项目配置到响应结构检查的完整流程。

## 1. 开始前

确认：

- API Prober 已安装并注册到当前客户端。
- 项目根目录存在 `.api-prober.json`。
- 项目配置不包含 Token 或密码。
- 目标是开发或测试环境。

## 2. 标准工作流

Agent 应按以下顺序工作：

```text
读取 .api-prober.json
  -> configure_session
  -> get_auth_status
  -> 必要时 authenticate
  -> http_request
  -> 必要时 inspect_response
  -> Agent 自行保存或更新项目快照
```

## 3. 探测无鉴权接口

项目配置：

```json
{
  "schemaVersion": 1,
  "projectKey": "local-demo",
  "allowedHosts": [],
  "authProfiles": {},
  "endpointRules": []
}
```

Agent 操作：

1. 读取配置并调用 `configure_session(config=...)`。
2. 调用：

```text
http_request(
  project_key="local-demo",
  method="GET",
  url="http://localhost:8080/api/users"
)
```

3. 查看 `response.body`、`processing.arrays` 和 `omitted_paths`。

回环地址无需额外 host 确认。

## 4. 使用用户名和密码登录

当 auth profile 配置了 `login`：

1. Agent 调用：

```text
authenticate(
  project_key="crm-dev",
  auth_profile_name="default",
  mode="login"
)
```

2. 客户端显示 URL elicitation。
3. 用户在本机页面输入登录字段。
4. Server 调用登录接口、提取 credential 并安全保存。
5. Agent 只收到 profile 状态，不会收到 Token。

随后请求：

```text
http_request(
  project_key="crm-dev",
  auth_profile_name="default",
  method="GET",
  url="http://crm-dev.internal:8080/api/profile"
)
```

非本机 HTTP 首次发送凭证时，用户需要确认明文传输风险。

## 5. 验证码或 SSO 系统

复杂认证不由 Server 自动完成。用户先通过现有系统获取 Token，再使用 manual 模式：

```text
authenticate(
  project_key="approval-test",
  auth_profile_name="manual",
  mode="manual"
)
```

用户在本机页面粘贴 Token。Token 不经过 Agent。

后续请求与 login 模式相同，只引用 profile name。

## 6. 探测 POST 查询接口

POST 即使语义上是查询，默认仍属于非只读方法。项目可以为明确端点配置：

```json
{
  "method": "POST",
  "origin": "http://crm-dev.internal:8080",
  "path": "/reports/query",
  "skipConfirmation": true,
  "timeoutSeconds": 60
}
```

调用：

```text
http_request(
  project_key="crm-dev",
  auth_profile_name="default",
  method="POST",
  url="http://crm-dev.internal:8080/reports/query",
  json_body={"page": 1, "pageSize": 20}
)
```

未配置规则时，Server 在发送请求前要求确认。

## 7. 查看大型 JSON 响应

默认整个工具结果预算为 20 KiB。数组只保留一个代表项。

如果结果返回：

```json
{
  "response_id": "resp_123",
  "omitted_paths": ["data.detail", "data.history"]
}
```

继续查看指定分支：

```text
inspect_response(
  response_id="resp_123",
  path="data.detail"
)
```

查看数组区间：

```text
inspect_response(
  response_id="resp_123",
  path="data.history",
  offset=0,
  limit=20,
  max_result_bytes=40960
)
```

单次预算仍不能超过项目、用户和 Server 上限。

## 8. 查看文本响应

HTML、XML 和普通文本按字符区间读取：

```text
inspect_response(
  response_id="resp_text",
  offset=0,
  limit=10000
)
```

二进制响应不会返回 Base64，只返回 Content-Type、大小和 SHA-256。

## 9. 处理认证失效

当响应命中 `invalidWhen`：

- HTTP 响应仍正常返回。
- profile 被标记为 `invalid`。
- 后续请求不再注入该 profile。

Agent 应：

1. 调用 `get_auth_status` 确认状态。
2. 调用 `authenticate` 重新登录或手动录入。
3. 新值保存成功后重新请求。

不要根据自然语言错误自行删除凭证。只有用户明确需要时才调用 `delete_auth_profile`。

## 10. 诊断失败请求

每个工具结果都包含 `request_id`。

查询对应日志：

```text
get_diagnostics(
  request_id="req_04",
  limit=200
)
```

需要记录某次请求经脱敏的输入输出时：

```text
http_request(
  project_key="crm-dev",
  method="GET",
  url="http://crm-dev.internal:8080/api/problem",
  debug=true
)
```

用户确认后，debug 只作用于该次请求。

## 11. Agent 管理项目快照

API Prober 不提供 `save_snapshot`。Agent 可以使用客户端自身的文件工具，将 MCP 结果或项目需要的简化结构写入项目，例如：

```text
docs/api-snapshots/users/list.json
```

建议快照至少保留：

- 请求 method、origin 和规范化 path。
- HTTP status 和 Content-Type。
- 采样后的 body。
- 数组原始长度和 sample index。
- redactions、truncations 和 omitted paths。
- 探测时间。

快照目录、命名、有效期和更新策略由项目及 Agent 决定，不属于 MCP 配置。

## 12. 推荐 Agent 指令

可以在项目说明文件中加入：

```markdown
## API 探测约定

1. 读取 `.api-prober.json` 并调用 `configure_session`。
2. 不向工具参数、项目文件或对话输出写入 Token、密码和 Cookie 值。
3. 优先读取项目已有接口快照；需要实时或缺失结构时调用 API Prober。
4. 大型响应使用 `inspect_response`，不要盲目提高结果预算。
5. 项目快照由当前 Agent 使用原生文件工具维护。
6. 工具失败时使用 `error.code` 和 `request_id`，必要时调用 `get_diagnostics`。
```

## 13. 安全使用建议

- 优先探测开发和测试环境。
- 对 `POST/PUT/PATCH/DELETE` 确认实际副作用。
- 不要为方便而使用过宽的 `/**` 免确认规则。
- HTTP 携带凭证时确认目标网络可信。
- 自签名 HTTPS 只在明确环境使用。
- 不要要求 Agent 输出或读取本地 profile 文件中的认证值。
- debug 日志只在复现问题时开启单次记录。
