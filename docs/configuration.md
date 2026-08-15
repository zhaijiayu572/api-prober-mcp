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
- 配置中禁止保存 Token、密码、Cookie 值和代理凭证。
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

数组下标不用于认证提取或失效规则。需要数组中的值时，应调整接口或使用手动认证。

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
      "login": null,
      "credentials": [],
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
| `login` | object/null | 否 | `null` | 自动登录配置。manual-only profile 使用 `null`。 |
| `credentials` | object[] | 是 | 无 | `1..5` 个认证值的提取和注入规则。 |
| `invalidWhen` | object | 否 | 仅 `401` | 认证失效判断。 |

`origin` 必须出现在项目 `allowedHosts` 中，或属于 Server 内置回环目标。

#### 4.5.2 `login`

```json
{
  "login": {
    "url": "http://dev-api.example.internal:8080/auth/login",
    "contentType": "json",
    "fields": [
      {
        "name": "username",
        "label": "Username",
        "type": "text",
        "required": true
      },
      {
        "name": "password",
        "label": "Password",
        "type": "password",
        "required": true
      }
    ],
    "staticBody": {
      "clientType": "web"
    }
  }
}
```

| 字段 | 类型 | 必填 | 允许值/约束 | 说明 |
|---|---|---:|---|---|
| `url` | string | 是 | 与 profile 同 origin 的完整 URL | 第一版固定使用 POST。 |
| `contentType` | string | 是 | `json`, `form` | 登录请求编码。 |
| `fields` | object[] | 是 | `1..20` | 本机敏感输入页面字段。 |
| `staticBody` | object | 否 | 扁平 JSON 标量 | 非敏感固定字段。 |

登录字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `name` | string | 是 | 发送给登录接口的字段名。 |
| `label` | string | 是 | 本机页面显示标签。 |
| `type` | string | 是 | `text` 或 `password`。 |
| `required` | boolean | 否 | 默认 `true`。 |

`staticBody` 禁止包含名称与登录字段相同的 key，禁止嵌套对象和数组，禁止秘密。

#### 4.5.3 `credentials`

```json
{
  "credentials": [
    {
      "name": "access-token",
      "extract": {
        "source": "json_body",
        "path": "data.accessToken"
      },
      "inject": {
        "type": "bearer",
        "name": "Authorization",
        "prefix": "Bearer "
      }
    }
  ]
}
```

credential 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `name` | string | 是 | profile 内唯一名称，也用于 manual 页面标签。 |
| `extract` | object/null | login 模式是 | 自动登录后的提取规则；manual-only 可为 `null`。 |
| `inject` | object | 是 | 请求注入规则。 |

`extract.source`：

| 值 | 附加字段 | 说明 |
|---|---|---|
| `json_body` | `path` | 从 JSON body 点路径提取。 |
| `response_header` | `name`, 可选 `stripPrefix` | 从响应 Header 提取。 |
| `set_cookie` | `name` | 从指定 Cookie 名提取。 |

`inject.type`：

| 值 | 字段 | 说明 |
|---|---|---|
| `bearer` | 可选 `name`, `prefix` | 默认 `Authorization` 和 `Bearer `。 |
| `header` | 必填 `name`, 可选 `prefix` | 注入自定义 Header。 |
| `cookie` | 必填 `name` | 注入 Cookie。 |

由 profile 管理的 Header/Cookie 不能被 `http_request.headers` 覆盖。

Set-Cookie 提取保留 Path、Secure、Expires 和 Max-Age。Domain 属性不能扩大 profile 的精确 origin 绑定；Path 不匹配或 Secure Cookie 用于 HTTP 时不注入。

#### 4.5.4 `invalidWhen`

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

已保存 credential 的精确值、Authorization、Cookie 和 Set-Cookie 不依赖该列表，始终脱敏。

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

### 5.2 Bearer 登录

```json
{
  "schemaVersion": 1,
  "projectKey": "crm-dev",
  "allowedHosts": ["http://crm-dev.internal:8080"],
  "authProfiles": {
    "default": {
      "origin": "http://crm-dev.internal:8080",
      "login": {
        "url": "http://crm-dev.internal:8080/auth/login",
        "contentType": "json",
        "fields": [
          {"name": "username", "label": "Username", "type": "text", "required": true},
          {"name": "password", "label": "Password", "type": "password", "required": true}
        ],
        "staticBody": {"clientType": "web"}
      },
      "credentials": [
        {
          "name": "access-token",
          "extract": {"source": "json_body", "path": "data.accessToken"},
          "inject": {"type": "bearer"}
        }
      ],
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
      "path": "/auth/login",
      "skipConfirmation": true
    },
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

### 5.3 自定义 Header 与手动 Token

```json
{
  "schemaVersion": 1,
  "projectKey": "approval-test",
  "allowedHosts": ["https://approval-test.example.com"],
  "authProfiles": {
    "manual": {
      "origin": "https://approval-test.example.com",
      "login": null,
      "credentials": [
        {
          "name": "x-token",
          "extract": null,
          "inject": {"type": "header", "name": "X-Token"}
        }
      ],
      "invalidWhen": {"statusCodes": [401], "bodyRules": []}
    }
  },
  "endpointRules": []
}
```

Agent 调用 `authenticate(mode="manual")` 后，用户在本机页面粘贴值。

### 5.4 Cookie + CSRF

```json
{
  "schemaVersion": 1,
  "projectKey": "legacy-test",
  "allowedHosts": ["http://legacy-test.internal"],
  "authProfiles": {
    "session": {
      "origin": "http://legacy-test.internal",
      "login": {
        "url": "http://legacy-test.internal/login",
        "contentType": "form",
        "fields": [
          {"name": "account", "label": "Account", "type": "text", "required": true},
          {"name": "password", "label": "Password", "type": "password", "required": true}
        ],
        "staticBody": {}
      },
      "credentials": [
        {
          "name": "session-cookie",
          "extract": {"source": "set_cookie", "name": "SESSION"},
          "inject": {"type": "cookie", "name": "SESSION"}
        },
        {
          "name": "csrf-token",
          "extract": {"source": "response_header", "name": "X-CSRF-Token"},
          "inject": {"type": "header", "name": "X-CSRF-Token"}
        }
      ],
      "invalidWhen": {"statusCodes": [401], "bodyRules": []}
    }
  },
  "endpointRules": []
}
```

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
