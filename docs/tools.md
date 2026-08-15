# MCP 工具参考

> 状态：实现契约。参数名、返回结构和错误码是第一版实现及测试依据。

API Prober 第一版暴露 7 个工具。所有工具返回 JSON object，不返回明文凭证。

## 1. 通用返回约定

成功结果至少包含：

```json
{
  "ok": true,
  "request_id": "req_..."
}
```

工具自身失败返回：

```json
{
  "ok": false,
  "request_id": "req_...",
  "error": {
    "code": "CONFIG_INVALID",
    "message": "Project configuration is invalid.",
    "details": {},
    "next_action": "Fix the reported field and call configure_session again."
  }
}
```

HTTP `4xx/5xx` 不属于工具失败，仍由 `http_request` 返回 `ok: true` 和真实状态码。

## 2. `configure_session`

加载 Agent 从项目 `.api-prober.json` 读取的配置。

### 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `config` | object | 是 | 完整项目配置对象，结构见 [配置参考](configuration.md)。 |

### 行为

- 严格校验 Schema 和字段。
- 计算规范化配置哈希。
- 对项目声明的非默认 origin 发起集中确认。
- 配置变化后撤销旧配置对应的临时授权。
- 不读取项目目录，不接受配置文件路径。

### 成功示例

```json
{
  "ok": true,
  "request_id": "req_01",
  "project_key": "crm-dev",
  "config_hash": "sha256:...",
  "approved_origins": ["http://crm-dev.internal:8080"],
  "auth_profiles": ["default"],
  "loaded_at": "2026-08-15T10:00:00Z",
  "global_config_hash": "sha256:..."
}
```

## 3. `authenticate`

通过自动登录或手动录入创建、替换 auth profile。

### 参数

| 参数 | 类型 | 必填 | 允许值 | 说明 |
|---|---|---:|---|---|
| `project_key` | string | 是 | 已配置项目 | 必须匹配当前会话配置。 |
| `auth_profile_name` | string | 是 | 已配置 profile | 目标 profile。 |
| `mode` | string | 是 | `login`, `manual` | 获取模式。 |

### 行为

- `login` 要求 profile 配置 `login`。
- `manual` 为每个 credential 显示敏感输入字段。
- 自动登录复用普通请求的 origin、HTTP/TLS、代理、重定向、超时和方法确认策略。
- Server 先完成所有必要风险确认，再打开敏感输入页面。
- 使用 URL elicitation 打开本机页面。
- 不向 Agent 返回输入值、登录 body 或提取后的认证值。
- 同一 profile 的 authenticate 串行执行。
- 成功后原子替换旧 profile 文件。
- 登录响应不写入 response cache，日志只记录提取和脱敏统计。

### 成功示例

```json
{
  "ok": true,
  "request_id": "req_02",
  "project_key": "crm-dev",
  "auth_profile_name": "default",
  "status": "valid",
  "credential_count": 1,
  "created_at": "2026-08-15T10:01:00Z",
  "expires_at": "2026-08-15T12:01:00Z"
}
```

`expires_at` 仅在能从 JWT `exp` 或响应属性可靠获得时返回。

## 4. `get_auth_status`

查询认证状态，不返回认证内容。

### 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `project_key` | string | 是 | 当前会话项目。 |
| `auth_profile_name` | string/null | 否 | 省略时返回当前项目的所有 profile 状态。 |

### 状态值

- `missing`
- `valid`
- `invalid`
- `expired`

### 成功示例

```json
{
  "ok": true,
  "request_id": "req_03",
  "profiles": [
    {
      "auth_profile_name": "default",
      "origin": "http://crm-dev.internal:8080",
      "status": "valid",
      "credential_count": 1,
      "created_at": "2026-08-15T10:01:00Z",
      "last_used_at": "2026-08-15T10:05:00Z",
      "expires_at": null
    }
  ]
}
```

## 5. `http_request`

执行受策略控制的 HTTP 请求并返回脱敏、采样后的结构。

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `project_key` | string | 是 | 无 | 当前会话项目。 |
| `method` | string | 是 | 无 | `GET`, `HEAD`, `OPTIONS`, `POST`, `PUT`, `PATCH`, `DELETE`。 |
| `url` | string | 是 | 无 | 完整 HTTP/HTTPS URL。 |
| `auth_profile_name` | string/null | 否 | `null` | 需要注入的 profile。 |
| `query` | object/null | 否 | `null` | query 参数；值允许标量或标量数组。 |
| `headers` | object/null | 否 | `null` | 普通业务 Header。 |
| `json_body` | JSON/null | 否 | `null` | JSON 请求体。 |
| `form_body` | object/null | 否 | `null` | URL encoded form。 |
| `raw_body` | string/null | 否 | `null` | 文本或明确约定的 Base64 原始内容。 |
| `raw_body_encoding` | string | 否 | `utf8` | `utf8` 或 `base64`；仅与 raw body 一起使用。 |
| `content_type` | string/null | raw 时是 | `null` | raw body 的 Content-Type。 |
| `timeout_seconds` | number/null | 否 | 配置解析值 | 单次总超时。 |
| `max_result_bytes` | integer/null | 否 | 配置解析值 | 整个工具结果预算。 |
| `sensitive_paths` | string[] | 否 | `[]` | 本次响应额外脱敏点路径。 |
| `debug` | boolean | 否 | `false` | 是否记录本次脱敏、裁剪后的输入输出；需要确认。 |

`json_body`、`form_body` 和 `raw_body` 互斥。

JSON 和 form 的 Content-Type 由 Server 设置。`headers` 不能覆盖该值；raw body 必须通过 `content_type` 明确声明。

### 确认顺序

一次请求可能触发多个风险。Server 应合并可合并的提示，但必须分别执行策略判断：

1. origin 是否已授权。
2. 是否命中 metadata 阻止。
3. 非只读方法是否需要确认。
4. 是否通过非本机 HTTP 发送认证信息。
5. 是否关闭 TLS 验证。
6. 是否通过代理发送认证信息。
7. 是否启用本次 debug capture。

任一必须确认项被拒绝时，请求不发送。

### 成功返回

```json
{
  "ok": true,
  "request_id": "req_04",
  "request": {
    "method": "GET",
    "origin": "http://crm-dev.internal:8080",
    "path": "/users/{id}",
    "attempts": 1,
    "queue_ms": 0,
    "duration_ms": 83,
    "tls_verified": null,
    "proxy_used": false
  },
  "response": {
    "status": 200,
    "detected_type": "json",
    "content_type": "application/json",
    "headers": {
      "Content-Type": "application/json",
      "X-Page-Count": "12"
    },
    "body": {
      "code": 0,
      "data": [
        {
          "id": 1,
          "name": "Alice"
        }
      ]
    },
    "size_bytes": 48211,
    "sha256": null
  },
  "processing": {
    "max_result_bytes": 20480,
    "arrays": [
      {
        "path": "data",
        "original_length": 100,
        "sample_index": 4
      }
    ],
    "redactions": [],
    "truncations": []
  },
  "redirects": [],
  "response_id": null,
  "omitted_paths": []
}
```

说明：

- `path` 优先使用命中的端点模板；未命中时使用规范化路径。
- HTTPS 返回 `tls_verified: true/false`；HTTP 返回 `null`。
- 二进制响应的 `body` 为 `null`，并提供 `sha256`。
- 超出结果预算时返回 `response_id` 和 `omitted_paths`。
- 命中认证失效规则时，响应仍返回，同时 profile 状态改为 `invalid`。

## 6. `inspect_response`

查看 `http_request` 缓存的大响应分支。

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `response_id` | string | 是 | 无 | 当前会话创建的响应 ID。 |
| `path` | string/null | 否 | `null` | JSON 点路径；文本响应不使用。 |
| `offset` | integer | 否 | `0` | 数组索引或文本字符偏移。 |
| `limit` | integer/null | 否 | 自动 | 数组元素数或文本字符数。 |
| `max_result_bytes` | integer/null | 否 | 配置解析值 | 本次结果预算。 |

### 行为

- JSON object 返回指定分支。
- JSON array 应用 offset/limit 后，结果仍执行代表项采样和预算。
- 文本使用字符 offset/limit。
- 二进制只返回元数据，不返回 Base64。
- response ID 不能跨 MCP 会话使用。

### 成功示例

```json
{
  "ok": true,
  "request_id": "req_05",
  "response_id": "resp_...",
  "path": "data.user.profile",
  "value": {},
  "processing": {
    "max_result_bytes": 20480,
    "arrays": [],
    "redactions": [],
    "truncations": []
  }
}
```

## 7. `delete_auth_profile`

删除本地 auth profile 文件。

### 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `project_key` | string | 是 | 当前项目。 |
| `auth_profile_name` | string | 是 | 目标 profile。 |

### 行为

- 删除前通过 elicitation 显示 project、profile 和 origin。
- 用户拒绝时不修改文件。
- profile 不存在时返回幂等成功，并标记 `already_missing: true`。

## 8. `get_diagnostics`

查询脱敏后的审计与诊断日志。

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `request_id` | string/null | 否 | `null` | 精确关联某次操作。 |
| `session_id` | string/null | 否 | `null` | 指定会话；`null` 表示时间窗口内所有本地会话。 |
| `project_key` | string/null | 否 | 当前项目 | 限制为指定项目；无活动项目时默认不过滤。 |
| `since` | string/null | 否 | 最近 1 小时 | ISO 8601 时间。 |
| `until` | string/null | 否 | 当前时间 | ISO 8601 时间。 |
| `level` | string/null | 否 | 全部 | `debug`, `info`, `warning`, `error`。 |
| `event_types` | string[] | 否 | `[]` | 空表示全部。 |
| `limit` | integer | 否 | `100` | 最大 `1..1000` 条。 |
| `max_result_bytes` | integer/null | 否 | 配置解析值 | 整个查询结果预算。 |

### 成功示例

```json
{
  "ok": true,
  "request_id": "req_06",
  "events": [
    {
      "timestamp": "2026-08-15T10:05:00Z",
      "level": "warning",
      "event_type": "auth_marked_invalid",
      "session_id": "session_...",
      "related_request_id": "req_04",
      "project_key": "crm-dev",
      "auth_profile_name": "default",
      "details": {
        "matched_status": 401
      }
    }
  ],
  "returned": 1,
  "truncated": false
}
```

## 9. 稳定错误码

| 错误码 | 触发条件 | 建议动作 |
|---|---|---|
| `CONFIG_NOT_LOADED` | 尚未成功 configure session | 读取项目配置并调用 `configure_session`。 |
| `CONFIG_INVALID` | 配置字段、类型、版本或规则冲突 | 修复 `details.path` 指向的配置。 |
| `PROJECT_MISMATCH` | 工具参数 project key 与会话不符 | 使用当前加载的 project key。 |
| `HOST_CONFIRMATION_DECLINED` | 用户拒绝 origin | 不发送请求，向用户说明。 |
| `HOST_NOT_APPROVED` | origin 未获授权且无法 elicitation | 使用支持 elicitation 的客户端。 |
| `METHOD_CONFIRMATION_DECLINED` | 用户拒绝非只读请求 | 不重试或绕过。 |
| `INSECURE_HTTP_DECLINED` | 用户拒绝明文发送凭证 | 改用 HTTPS 或无鉴权请求。 |
| `INSECURE_TLS_DECLINED` | 用户拒绝未验证 TLS | 修复证书或启用验证。 |
| `PROXY_CONFIRMATION_DECLINED` | 用户拒绝经代理发送凭证 | 禁用代理或不使用认证。 |
| `DEBUG_CONFIRMATION_DECLINED` | 用户拒绝 debug capture | 使用普通日志继续诊断。 |
| `METADATA_TARGET_BLOCKED` | 命中 metadata 硬阻止 | 仅在确有需要时使用用户级危险覆盖。 |
| `AUTH_PROFILE_NOT_FOUND` | 配置中不存在 profile | 修复 profile 名称。 |
| `AUTH_REQUIRED` | profile 本地缺失 | 调用 `authenticate`。 |
| `AUTH_INVALID` | profile 已被标记 invalid | 重新 authenticate。 |
| `AUTH_EXPIRED` | 已知过期 | 重新 authenticate。 |
| `AUTH_INPUT_CANCELLED` | 用户取消本机输入 | 保持原 profile 不变。 |
| `AUTH_INPUT_TIMEOUT` | 本机输入页面超过 5 分钟 | 重新调用 authenticate。 |
| `AUTH_EXTRACTION_FAILED` | 登录成功但提取规则未命中 | 检查登录响应结构和配置路径。 |
| `REQUEST_INVALID` | URL、Header、body 参数不合法 | 修复参数。 |
| `REQUEST_TIMEOUT` | 排队或请求超过总超时 | 调整 timeout 或检查服务。 |
| `REQUEST_FAILED` | DNS、连接或协议错误 | 使用 diagnostics 定位。 |
| `REDIRECT_LIMIT_EXCEEDED` | 超过 5 次或循环 | 检查接口或网关配置。 |
| `RESPONSE_TOO_LARGE` | 下载超过 10 MiB | 缩小接口响应或调整设计，不能单次突破硬上限。 |
| `RESPONSE_NOT_FOUND` | response ID 不存在、过期或跨会话 | 重新请求接口。 |
| `RESULT_BUDGET_INVALID` | 结果预算低于 4 KiB 或超过允许上限 | 使用合法范围。 |
| `DIAGNOSTICS_QUERY_INVALID` | 日志过滤参数无效 | 修正时间和 limit。 |
| `STORAGE_PERMISSION_INVALID` | 用户数据权限过宽 | 按提示修复为 0700/0600。 |
| `STORAGE_ERROR` | 文件锁、写入或原子替换失败 | 查看 diagnostics 和磁盘状态。 |
| `ELICITATION_UNSUPPORTED` | 客户端不支持所需 elicitation | 使用受支持版本的 Claude Code/Codex。 |
| `INTERNAL_ERROR` | 未预期内部错误 | 使用 request ID 查询 diagnostics 并报告。 |

## 10. 兼容性规则

- 第一版工具参数和返回字段遵循向后兼容原则。
- 新增可选字段不提升主版本。
- 删除字段、改变含义或收紧已公开枚举需要主版本升级。
- Agent 不应依赖自然语言 `message` 做分支，应使用 `ok`、`error.code`、HTTP status 和结构化字段。
