# API Prober 配置参考

> 状态：实现契约。当前代码尚未实现，本文定义第一版必须支持的配置结构。

本文是配置字段的权威参考。架构与安全理由见 [需求与架构设计](../api-prober-mcp-design.md)。

## 1. 配置层级

API Prober 使用三层配置：

```text
单次 MCP 工具参数
  -> 项目 .api-prober.json，经 configure_session 注入
  -> ~/.api-prober-mcp/config.json
  -> Server 内置默认值与硬限制
```

- 高优先级配置可以收紧低优先级配置或覆盖普通默认值。
- 任何层级都不能突破 Server 硬限制。
- 项目配置不能设置代理、危险 metadata 覆盖或自动开启 debug。
- 配置中禁止保存 Token、Cookie 值和代理凭证。
- 所有大小字段使用字节，按 UTF-8 序列化后的大小计算。

## 2. 通用约定

### 2.1 Schema 版本

两个配置文件都必须包含：

```json
{
  "schemaVersion": 1
}
```

未知字段、错误类型和不支持的版本直接返回 `CONFIG_INVALID`。

### 2.2 Origin

本文中的 origin 是：

```text
scheme + host + port
```

示例：

```text
http://localhost:8080
https://api.example.com
https://api.example.com:8443
```

- 只支持 `http` 和 `https`。
- 不允许包含 path、query、fragment、userinfo。
- 默认端口参与规范化：HTTPS 的 443 和 HTTP 的 80 可以省略。
- host 统一执行小写、IDNA 和 DNS 安全检查。

配置字段沿用 `allowedHosts` 名称，但值必须是精确 origin。

### 2.3 JSON path

第一版使用简单点路径，不支持 JSONPath 表达式：

```text
data.accessToken
result.session.ticket
code
```

数组下标不用于失效规则。

### 2.4 Path pattern

端点规则使用受限路径模板：

- `/users/{id}`：匹配一个路径段。
- `/reports/**`：匹配零个或多个后续路径段。
- `/health`：精确匹配。

不支持正则表达式、host 通配符或 query 匹配。

## 3. 用户级配置

路径：

```text
~/.api-prober-mcp/config.json
```

Server 启动时读取一次。修改后需要重启 Claude Code 或 Codex 的对应 MCP 会话。

用户级配置无效时，Server 只允许返回配置错误和读取 diagnostics，拒绝鉴权及网络请求。

### 3.1 完整结构

```json
{
  "schemaVersion": 1,
  "allowedHosts": [],
  "limits": {
    "globalConcurrency": 8,
    "perOriginConcurrency": 4,
    "defaultTimeoutSeconds": 30,
    "maxTimeoutSeconds": 180,
    "defaultResultBytes": 20480,
    "maxResultBytes": 102400,
    "maxResponseBytes": 10485760,
    "maxSessionCacheBytes": 104857600
  },
  "logging": {
    "level": "info",
    "retentionDays": 7,
    "maxBytes": 52428800
  },
  "proxy": null,
  "response": {
    "allowedHeaders": []
  },
  "dangerousOverrides": {
    "metadataHosts": []
  }
}
```

### 3.2 顶层字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `schemaVersion` | integer | 是 | 无 | 固定为 `1`。 |
| `allowedHosts` | string[] | 否 | `[]` | 用户级自动信任的精确 origin。回环地址由 Server 内置，无需重复配置。 |
| `limits` | object | 否 | 见下文 | 全局并发、超时、结果和缓存限制。 |
| `logging` | object | 否 | 见下文 | 日志级别和保留策略。 |
| `proxy` | object/null | 否 | `null` | 显式 HTTP 代理。不会读取系统代理变量。 |
| `response` | object | 否 | 见下文 | 全局允许返回的额外响应 Header。 |
| `dangerousOverrides` | object | 否 | 空列表 | 云 metadata 目标的危险覆盖。仍需会话确认。 |

### 3.3 `limits`

| 字段 | 类型 | 默认值 | 允许范围 | 说明 |
|---|---|---:|---:|---|
| `globalConcurrency` | integer | `8` | `1..8` | 所有 origin 的执行中请求总数。只能降低 Server 硬上限。 |
| `perOriginConcurrency` | integer | `4` | `1..4` | 单一 origin 的执行中请求数。 |
| `defaultTimeoutSeconds` | number | `30` | `1..300` | 未被项目或单次调用覆盖时的总超时。 |
| `maxTimeoutSeconds` | number | `180` | `1..300` | 项目和单次调用可使用的最大超时。不得小于默认超时。 |
| `defaultResultBytes` | integer | `20480` | `4096..1048576` | 默认整个 MCP 工具结果预算。 |
| `maxResultBytes` | integer | `102400` | `4096..1048576` | 项目和单次调用可使用的最大结果预算。 |
| `maxResponseBytes` | integer | `10485760` | `4096..10485760` | 单个 HTTP 响应的最大下载量。 |
| `maxSessionCacheBytes` | integer | `104857600` | `1048576..104857600` | 单个 MCP 会话的响应缓存总上限。 |

Server 硬限制：

- 超时：300 秒。
- 工具结果：1 MiB。
- 单响应下载：10 MiB。
- 单会话缓存：100 MiB。
- 全局并发：8。
- 单 origin 并发：4。

### 3.4 `logging`

| 字段 | 类型 | 默认值 | 允许值 | 说明 |
|---|---|---|---|---|
| `level` | string | `info` | `debug`, `info`, `warning`, `error` | 控制普通诊断事件详细程度。敏感值始终不记录。 |
| `retentionDays` | integer | `7` | `1..90` | 日志文件最长保留天数。 |
| `maxBytes` | integer | `52428800` | `1048576..1073741824` | 所有日志的总大小上限。 |

`level: debug` 不等于记录业务 body。只有 `http_request(debug=true)` 且用户确认后，才记录该次调用经脱敏和裁剪的输入输出。

### 3.5 `proxy`

```json
{
  "proxy": {
    "url": "http://proxy.example.com:8080"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `url` | string | 是 | HTTP 或 HTTPS 代理 URL。禁止 userinfo 和内嵌凭证。 |

- `localhost`、`127.0.0.1` 和 `::1` 永远直连。
- 第一次通过代理发送认证信息时，当前会话必须确认。
- 第一版不支持认证代理。

### 3.6 `response.allowedHeaders`

追加到默认响应 Header 允许名单：

```json
{
  "response": {
    "allowedHeaders": ["X-Page-Count", "X-Next-Cursor"]
  }
}
```

类型：`string[]`。Header 名不区分大小写，重复项规范化后去重。

### 3.7 `dangerousOverrides.metadataHosts`

```json
{
  "dangerousOverrides": {
    "metadataHosts": ["http://169.254.169.254"]
  }
}
```

- 类型：`string[]`。
- 只接受精确 origin。
- 该配置仅允许进入会话确认流程，不代表自动授权。
- 项目配置和单次请求不能设置该字段。

## 4. 项目配置

建议路径：

```text
<project-root>/.api-prober.json
```

Agent 读取并将完整对象传给 `configure_session`。Server 不自行读取该文件。

### 4.1 完整结构

```json
{
  "schemaVersion": 1,
  "projectKey": "example-dev",
  "allowedHosts": ["http://dev-api.example.internal:8080"],
  "defaults": {
    "defaultTimeoutSeconds": 30,
    "maxTimeoutSeconds": 120,
    "defaultResultBytes": 20480,
    "maxResultBytes": 102400
  },
  "tls": [],
  "authProfiles": {},
  "endpointRules": [],
  "response": {
    "allowedHeaders": [],
    "sensitivePaths": []
  }
}
```

### 4.2 顶层字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `schemaVersion` | integer | 是 | 无 | 固定为 `1`。 |
| `projectKey` | string | 是 | 无 | 项目和环境的稳定标识，例如 `crm-dev`。 |
| `allowedHosts` | string[] | 否 | `[]` | 项目希望访问的精确 origin。首次加载或配置变化后需确认。 |
| `defaults` | object | 否 | 用户级默认 | 项目的超时和结果预算。 |
| `tls` | object[] | 否 | `[]` | 精确 origin 的证书验证策略。 |
| `authProfiles` | object | 否 | `{}` | 以 profile name 为 key 的鉴权配置。 |
| `endpointRules` | object[] | 否 | `[]` | 非只读确认、超时和结果预算规则。 |
| `response` | object | 否 | 见下文 | 响应 Header 和敏感路径配置。 |

`projectKey` 约束：

- 长度 `1..64`。
- 允许 ASCII 字母、数字、点、下划线和连字符。
- 建议包含环境，例如 `example-dev`、`example-test`。

### 4.3 `defaults`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `defaultTimeoutSeconds` | number | 用户级默认 | 项目默认总超时。 |
| `maxTimeoutSeconds` | number | 用户级上限 | 项目允许的最大总超时。 |
| `defaultResultBytes` | integer | 用户级默认 | 项目默认工具结果预算。 |
| `maxResultBytes` | integer | 用户级上限 | 项目允许的最大工具结果预算。 |

这些值不得超过用户级和 Server 上限。

### 4.4 `tls`

```json
{
  "tls": [
    {
      "origin": "https://dev-api.example.internal:8443",
      "verify": false
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `origin` | string | 是 | 精确 HTTPS origin，必须同时出现在 `allowedHosts`。 |
| `verify` | boolean | 是 | `false` 表示关闭证书验证，并触发每会话确认。 |

HTTP origin 不得出现在 `tls` 中。

### 4.5 `authProfiles`

```json
{
  "authProfiles": {
    "default": {
      "origin": "http://dev-api.example.internal:8080",
      "type": "bearer",
      "bearer": {
        "headerName": "Authorization",
        "prefix": "Bearer "
      },
      "invalidWhen": {
        "statusCodes": [401],
        "bodyRules": []
      }
    }
  }
}
```

profile name 约束与 `projectKey` 相同，长度 `1..64`。

#### 4.5.1 auth profile 字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `origin` | string | 是 | 无 | 认证信息绑定的精确 origin。 |
| `type` | string | 是 | 无 | `bearer`、`header` 或 `cookie`。由 Agent 根据项目代码判断并配置。 |
| `bearer` | object | `type=bearer` 时是 | 无 | Bearer Header 注入配置。 |
| `header` | object | `type=header` 时是 | 无 | 自定义 Header 注入配置。 |
| `cookie` | object | `type=cookie` 时是 | 无 | Cookie jar 和 CSRF Header 配置。 |
| `invalidWhen` | object | 否 | 仅 `401` | 认证失效判断。 |

`origin` 必须出现在项目 `allowedHosts` 中，或属于 Server 内置回环目标。

每个 profile 必须且只能配置一种认证类型。严格校验规则：

- `type` 对应的同名对象必须存在。
- 另外两个类型对象必须不存在。
- 不接受未知类型、未知字段或多个认证类型对象。
- `.api-prober.json` 只保存注入方式，不保存 Token 或 Cookie 值。
- Agent 修改 `type` 或其注入对象后，已保存值因配置摘要不匹配而停止使用，必须重新调用 `set_auth`。

#### 4.5.2 `bearer`

```json
{
  "type": "bearer",
  "bearer": {
    "headerName": "Authorization",
    "prefix": "Bearer "
  }
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `headerName` | string | 否 | `Authorization` | 注入 Token 的 Header 名。 |
| `prefix` | string | 否 | `Bearer ` | Token 前缀；允许空字符串。 |

用户只粘贴 Token 本体。Server 组合 `prefix + value`，不要求用户重复输入 `Bearer `。

#### 4.5.3 `header`

```json
{
  "type": "header",
  "header": {
    "name": "X-Token",
    "prefix": ""
  }
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `name` | string | 是 | 无 | 注入值的 Header 名。 |
| `prefix` | string | 否 | `""` | 值前缀。 |

#### 4.5.4 `cookie`

```json
{
  "type": "cookie",
  "cookie": {
    "defaultPath": "/",
    "csrfHeaders": [
      {
        "cookieName": "XSRF-TOKEN",
        "headerName": "X-XSRF-TOKEN",
        "decode": "url"
      }
    ]
  }
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `defaultPath` | string | 否 | `/` | 手工录入 Cookie 的默认 Path，必须以 `/` 开头。 |
| `csrfHeaders` | object[] | 否 | `[]` | 从 cookie jar 派生的 CSRF Header，最多 10 项。 |

`csrfHeaders` 条目：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `cookieName` | string | 是 | 无 | 作为值来源的 Cookie 名。 |
| `headerName` | string | 是 | 无 | 请求时注入的 Header 名。 |
| `decode` | string | 否 | `none` | `none` 或 `url`；`url` 表示注入前进行一次 URL decode。 |

Cookie 用户输入接受带或不带 `Cookie:` 前缀的 Cookie Header 值。总输入最大 16 KiB，最多 50 个 Cookie；拒绝 CRLF、控制字符、非法 Cookie 名和无法解析的条目。

手工 Cookie 进入绑定精确 origin 的 cookie jar。同 origin 的 `Set-Cookie` 可以更新、轮换或删除条目，并保留 Path、Secure、Expires 和 Max-Age；Domain 不得扩大 profile 的 origin 绑定。Path 不匹配或 Secure Cookie 用于 HTTP 时不注入，跨 origin 重定向移除全部 Cookie。日志和工具结果只允许出现 Cookie 名、数量和更新动作。

CSRF Header 只能从当前 cookie jar 中的命名 Cookie 派生。第一版不支持响应 Header、HTML 或其他来源的 CSRF 值。

派生 CSRF Header 属于 `cookie` 类型内部行为，不构成第二种认证类型，也不保存独立值。

由 profile 管理的 Header、Cookie 和派生 CSRF Header 不能被 `http_request.headers` 覆盖。

#### 4.5.5 `invalidWhen`

```json
{
  "invalidWhen": {
    "statusCodes": [401],
    "bodyRules": [
      {
        "path": "code",
        "equals": 401
      },
      {
        "path": "error.code",
        "equals": "TOKEN_EXPIRED"
      }
    ]
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `statusCodes` | integer[] | `[401]` | 任一状态码命中即标记 invalid。不要默认加入 403。 |
| `bodyRules` | object[] | `[]` | JSON body 规则，任一命中即失效。 |

body rule 的 `equals` 允许 string、number、boolean 或 null。

### 4.6 `endpointRules`

```json
{
  "endpointRules": [
    {
      "method": "POST",
      "origin": "http://dev-api.example.internal:8080",
      "path": "/reports/query",
      "skipConfirmation": true,
      "timeoutSeconds": 90,
      "maxResultBytes": 40960
    }
  ]
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `method` | string | 是 | 无 | `GET`, `HEAD`, `OPTIONS`, `POST`, `PUT`, `PATCH`, `DELETE`。 |
| `origin` | string | 是 | 无 | 精确 origin。 |
| `path` | string | 是 | 无 | 受限路径模板。 |
| `skipConfirmation` | boolean | 否 | `false` | 非只读请求是否免除方法确认。不会免除 host、HTTP/TLS 等确认。 |
| `timeoutSeconds` | number | 否 | 无 | 端点超时覆盖。 |
| `maxResultBytes` | integer | 否 | 无 | 端点结果预算覆盖。 |

重复规则和无法确定“最具体规则”的冲突配置直接拒绝加载。

`origin` 必须出现在项目 `allowedHosts` 中，或属于 Server 内置回环目标。

### 4.7 `response`

```json
{
  "response": {
    "allowedHeaders": ["X-Page-Count"],
    "sensitivePaths": [
      "data.session.ticket",
      "debug.credentials"
    ]
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `allowedHeaders` | string[] | `[]` | 追加允许返回的响应 Header。 |
| `sensitivePaths` | string[] | `[]` | 所有普通响应默认脱敏的点路径。 |

已保存认证值的精确值、Authorization、Cookie 和 Set-Cookie 不依赖该列表，始终脱敏。

## 5. 完整示例

### 5.1 最小本机无鉴权项目

```json
{
  "schemaVersion": 1,
  "projectKey": "local-demo",
  "allowedHosts": [],
  "authProfiles": {},
  "endpointRules": []
}
```

回环地址由 Server 内置允许。

### 5.2 Bearer Token

```json
{
  "schemaVersion": 1,
  "projectKey": "crm-dev",
  "allowedHosts": ["http://crm-dev.internal:8080"],
  "authProfiles": {
    "default": {
      "origin": "http://crm-dev.internal:8080",
      "type": "bearer",
      "bearer": {
        "headerName": "Authorization",
        "prefix": "Bearer "
      },
      "invalidWhen": {
        "statusCodes": [401],
        "bodyRules": [{"path": "code", "equals": 401}]
      }
    }
  },
  "endpointRules": [
    {
      "method": "POST",
      "origin": "http://crm-dev.internal:8080",
      "path": "/reports/query",
      "skipConfirmation": true,
      "timeoutSeconds": 60
    }
  ]
}
```

Agent 从项目代码识别出 `Authorization: Bearer <token>` 后写入该配置。调用 `set_auth` 时，用户只粘贴 Token 本体。

### 5.3 自定义 Header

```json
{
  "schemaVersion": 1,
  "projectKey": "approval-test",
  "allowedHosts": ["https://approval-test.example.com"],
  "authProfiles": {
    "manual": {
      "origin": "https://approval-test.example.com",
      "type": "header",
      "header": {
        "name": "X-Token",
        "prefix": ""
      },
      "invalidWhen": {"statusCodes": [401], "bodyRules": []}
    }
  },
  "endpointRules": []
}
```

Agent 从请求拦截器或 API 客户端代码识别 Header 名后写入配置，再调用 `set_auth`。用户无需在页面选择认证方式。

### 5.4 Cookie + CSRF

```json
{
  "schemaVersion": 1,
  "projectKey": "legacy-test",
  "allowedHosts": ["http://legacy-test.internal"],
  "authProfiles": {
    "session": {
      "origin": "http://legacy-test.internal",
      "type": "cookie",
      "cookie": {
        "defaultPath": "/",
        "csrfHeaders": [
          {
            "cookieName": "XSRF-TOKEN",
            "headerName": "X-XSRF-TOKEN",
            "decode": "url"
          }
        ]
      },
      "invalidWhen": {"statusCodes": [401], "bodyRules": []}
    }
  },
  "endpointRules": []
}
```

用户可以粘贴 `Cookie: SESSION=abc123; XSRF-TOKEN=xyz789`。Server 保存为 cookie jar，并在请求时从 `XSRF-TOKEN` 派生 `X-XSRF-TOKEN` Header。

### 5.5 自签名 HTTPS

```json
{
  "schemaVersion": 1,
  "projectKey": "self-signed-test",
  "allowedHosts": ["https://api.test.internal:8443"],
  "tls": [
    {"origin": "https://api.test.internal:8443", "verify": false}
  ],
  "authProfiles": {},
  "endpointRules": []
}
```

首次使用时仍需会话确认。

## 6. 配置维护规则

- 修改安全相关字段后，Agent 重新调用 `configure_session`。
- Server 根据规范化配置计算哈希；哈希变化使项目 host 授权失效。
- 不要在 `.api-prober.json` 中写开发者个人 Token。
- 不要使用 403 作为通用 Token 过期规则，除非目标系统明确如此定义。
- 非只读免确认规则应尽量使用精确 path，谨慎使用 `/**`。
- `verify: false` 只用于明确的开发/测试环境。
- 结果预算优先保持较小；需要更多结构时使用 `inspect_response`。
