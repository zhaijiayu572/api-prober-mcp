# API Prober MCP Server 设计方案

> 背景：vibe coding 时复用已有接口，因缺乏规范文档而不得不手动在浏览器开发者工具中截取请求来获知返回格式。本方案通过一个全局 MCP Server 让 Agent 具备自主探测接口能力。

## 核心目标

- Agent 能自主发 HTTP 请求探测接口返回结构，无需人工介入
- 一次配置，所有项目通用
- 探测结果持久化为快照，避免重复探测

---

## 架构设计

```
~/.claude/
  settings.json                        ← 全局 MCP 注册（一次配置）
  mcp-servers/
    api-prober/
      server.py                        ← MCP server 主体
      requirements.txt                 ← 依赖（fastmcp, httpx）
      tokens.json                      ← 各项目 token 存储（本地，不入 git）
      README.md

各业务项目/
  docs/api-snapshots/                  ← 接口返回快照（可入 git，作为文档）
    monitor/queryXxx.json
    approval/submitReview.json
  CLAUDE.md                            ← 加几行约定即可接入，不动业务代码
```

---

## MCP Server 暴露的工具

| 工具名 | 参数 | 作用 |
|--------|------|------|
| `http_request` | `method, url, headers, body` | 发任意 HTTP 请求，返回 status + body |
| `save_snapshot` | `data, save_path` | 把响应存到指定路径（通常是项目的 `docs/api-snapshots/`） |
| `set_token` | `project_key, token` | 以项目 key 存储 token |
| `get_token` | `project_key` | 读取已存的 token |
| `login_and_save` | `login_url, credentials, token_field_path, project_key` | POST 登录 → 按字段路径提取 token → 存储 |

---

## Agent 工作流

```
1. 读 CLAUDE.md，获知登录 URL、token 字段路径、项目 key
2. 调用 login_and_save → 获取并存储 token
3. 调用 http_request（带 token header）→ 探测目标接口
4. 调用 save_snapshot → 把返回结构写入 docs/api-snapshots/
5. 基于快照继续开发，后续同接口直接读快照，不重复探测
```

---

## 全局注册方式

```json
// ~/.claude/settings.json
{
  "mcpServers": {
    "api-prober": {
      "command": "python3",
      "args": ["/home/{user}/.claude/mcp-servers/api-prober/server.py"]
    }
  }
}
```

注册一次，所有项目的 Claude Code session 均可调用。

---

## 业务项目接入方式

每个项目只需在 `CLAUDE.md` 追加一个约定块，**不修改任何业务代码，不增加任何项目依赖**：

```markdown
## API 探测约定

- 登录接口：`POST http://<host>/auth/login`
- token 字段路径：`data.token`（支持嵌套，如 `result.data.accessToken`）
- 项目 key（token 存储标识）：`<project-name>-dev`
- 遇到不熟悉的接口返回格式，优先查 `docs/api-snapshots/`
- 快照不存在时，使用 api-prober MCP 工具探测并存入
```

---

## 技术选型

| 项 | 选择 | 原因 |
|----|------|------|
| 语言 | Python 3 | 依赖少，MCP 生态成熟 |
| MCP 框架 | `fastmcp` | 极简 decorator 风格，~100 行可完成 |
| HTTP 客户端 | `httpx` | 同步/异步均支持，比 requests 更现代 |
| Token 存储 | 本地 `tokens.json` | 无需额外服务，足够简单 |

### 核心实现骨架

```python
from fastmcp import FastMCP
import httpx, json, os

mcp = FastMCP("api-prober")

@mcp.tool()
def http_request(method: str, url: str, headers: dict = None, body: dict = None) -> dict:
    """发送 HTTP 请求，返回 {status, headers, body}"""
    ...

@mcp.tool()
def login_and_save(login_url: str, credentials: dict, token_field_path: str, project_key: str) -> str:
    """登录并持久化 token，返回 token 值"""
    ...

@mcp.tool()
def get_token(project_key: str) -> str:
    """读取已存储的 token"""
    ...

@mcp.tool()
def save_snapshot(data: dict, save_path: str) -> str:
    """将接口返回写入快照文件"""
    ...
```

---

## 后续开发计划

开一个独立仓库维护此 MCP server，建议仓库结构：

```
api-prober-mcp/
  server.py               ← MCP server 入口
  tools/
    http_tool.py          ← http_request, save_snapshot
    auth_tool.py          ← login_and_save, set_token, get_token
  storage/
    token_store.py        ← token 读写封装
  requirements.txt
  README.md               ← 安装说明 + 各项目接入示例
  CHANGELOG.md
```

### 可扩展方向

- 支持 OAuth2 / Bearer / Cookie 等多种鉴权模式
- 支持请求前自动检查 token 是否过期并刷新
- 快照 diff：对比两次探测结果，识别接口变更
- 批量探测：读取 OpenAPI/Swagger 文档自动生成快照
