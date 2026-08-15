# 安装与客户端注册

> 状态：How-to 实现契约。安装脚本和可执行入口将在代码实现阶段提供；当前文档中的项目命令尚不可执行。

## 1. 前置条件

- Linux。
- Python 3.12。
- `python3.12-venv`。
- Claude Code 或 Codex CLI 的受支持版本。
- 客户端必须支持 MCP form elicitation 和 URL elicitation。

Ubuntu/Debian 缺少 venv 时：

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
```

除系统 venv 包外，项目安装不需要 sudo。

## 2. 默认安装

从仓库根目录执行：

```bash
./scripts/install.sh
```

安装程序应：

1. 创建 `/home/<user>/.api-prober-mcp/` 和安全子目录。
2. 创建 `runtime/venv`。
3. 升级 venv 内的 pip。
4. 安装当前仓库包和锁定依赖。
5. 初始化缺失的 `config.json`。
6. 校验目录和文件权限。
7. 不修改 Claude Code 或 Codex 配置。

安装后预期入口：

```text
~/.api-prober-mcp/runtime/venv/bin/api-prober-mcp
```

## 3. 安装并注册客户端

显式注册 Claude Code 和 Codex：

```bash
./scripts/install.sh --register claude,codex
```

规则：

- 注册名固定为 `api-prober`。
- 注册前检查同名 MCP Server。
- 已存在时停止并提示，不自动覆盖。
- 一个客户端注册失败时，报告精确客户端和命令，不隐藏另一个客户端的结果。

## 4. 手动注册

### 4.1 Claude Code

```bash
claude mcp add --scope user api-prober -- \
  /home/<user>/.api-prober-mcp/runtime/venv/bin/api-prober-mcp
```

检查：

```bash
claude mcp get api-prober
```

### 4.2 Codex

```bash
codex mcp add api-prober -- \
  /home/<user>/.api-prober-mcp/runtime/venv/bin/api-prober-mcp
```

检查：

```bash
codex mcp get api-prober
```

不要把 Token、Cookie 值或代理凭证放入客户端 MCP 环境变量。

## 5. 用户级配置

首次安装生成：

```text
~/.api-prober-mcp/config.json
```

最小配置：

```json
{
  "schemaVersion": 1
}
```

完整字段见 [配置参考](configuration.md)。修改后重启对应 MCP 会话。

## 6. 项目接入

在业务项目根目录创建 `.api-prober.json`。该文件不包含秘密，可以提交 Git。

最小示例：

```json
{
  "schemaVersion": 1,
  "projectKey": "example-dev",
  "allowedHosts": ["http://dev-api.example.internal:8080"],
  "authProfiles": {},
  "endpointRules": []
}
```

Agent 工作约定可以放入项目的 `CLAUDE.md` 或 `AGENTS.md`：

```markdown
## API 探测约定

- 使用 API Prober 前读取项目根目录的 `.api-prober.json`。
- 调用 `configure_session` 后再执行鉴权或请求。
- 遇到未知响应结构时使用 `http_request`，大型响应使用 `inspect_response`。
- 接口快照由当前 Agent 使用项目文件工具管理，API Prober 不写项目文件。
```

## 7. 升级

在新版仓库根目录重复执行：

```bash
./scripts/install.sh
```

升级应：

- 原地更新 runtime 中的包。
- 保留 `config.json`、credentials、logs 和未过期 cache。
- 升级前校验配置 Schema。
- 不自动迁移未知或不兼容的配置版本。
- 不重复注册已存在的客户端配置。

需要同步检查注册时：

```bash
./scripts/install.sh --register claude,codex
```

同名配置存在时仍不得自动覆盖。

## 8. 卸载

卸载 runtime 并注销客户端，保留用户数据：

```bash
./scripts/uninstall.sh
```

默认保留：

- `config.json`
- `credentials/`
- `logs/`

彻底删除全部数据：

```bash
./scripts/uninstall.sh --purge-data
```

`--purge-data` 必须显示将被删除的精确目录并再次请求确认。取消确认时不得删除任何数据。

## 9. 权限检查

预期权限：

```text
~/.api-prober-mcp/                         0700
~/.api-prober-mcp/credentials/             0700
~/.api-prober-mcp/credentials/profiles/*   0600
~/.api-prober-mcp/logs/                    0700
~/.api-prober-mcp/cache/                   0700
~/.api-prober-mcp/config.json              0600
```

权限过宽时，Server 可以启动并提供诊断，但必须拒绝读取或写入凭证。

## 10. 安装验收

实现完成后执行：

```bash
~/.api-prober-mcp/runtime/venv/bin/api-prober-mcp --version
```

然后分别在 Claude Code 和 Codex 中验证：

1. 能发现 7 个工具。
2. `configure_session` 能加载回环地址项目。
3. 非默认 origin 能显示确认。
4. `set_auth` 能显示由配置确定认证类型的 URL elicitation。
5. `http_request` 能返回结构化结果。
6. `get_diagnostics` 能按 request ID 查询日志。

## 11. 常见问题

### `ELICITATION_UNSUPPORTED`

升级 Claude Code 或 Codex。第一版不提供非 elicitation fallback。

### `STORAGE_PERMISSION_INVALID`

按工具返回的精确路径修复为 `0700` 或 `0600`，然后重启会话。

### 修改 `config.json` 未生效

用户级配置只在 stdio Server 启动时加载。重启对应客户端 MCP 会话。

### 已存在同名 `api-prober`

先使用客户端命令检查现有配置。安装脚本不会自动覆盖，避免破坏用户自定义设置。
