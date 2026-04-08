# Odoo 18 MCP Server (JSON-2 API)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white)](https://www.python.org/)

Odoo 18 MCP Server，使用 JSON-2 API 連線。

Based on [twtrubiks/odoo19-mcp-server](https://github.com/twtrubiks/odoo19-mcp-server), adapted for Odoo 18.

## 技術棧

- **Python**: 3.13
- **FastMCP**: >=3.0.0,<4.0.0
- **odoo-client-lib**: 2.0.1 (JSON-2 API)

## 架構

```mermaid
flowchart TB
    subgraph Client["MCP Client"]
        CC[Claude Code]
        GC[Gemini CLI]
        MI[MCP Inspector]
    end

    subgraph Server["MCP Server (FastMCP)"]
        R[Resources<br/>odoo://models<br/>odoo://user<br/>odoo://company]
        T[Tools<br/>search_records<br/>create_record<br/>update_record]
        DI[Dependency Injection<br/>get_shared_client]
    end

    subgraph RPC["OdooJsonRpcClient"]
        OL[odoolib<br/>json2/json2s protocol]
    end

    subgraph Odoo["Odoo 18 Server"]
        EP["/jsonrpc endpoint"]
    end

    Client -->|MCP Protocol<br/>stdio/http/sse| Server
    R --> DI
    T --> DI
    DI --> RPC
    RPC -->|HTTP/HTTPS| Odoo
```

## 與 Odoo 19 版本的差異

| 項目 | Odoo 19 | Odoo 18 |
|------|---------|---------|
| Record URL 格式 | `/odoo/{model}/{id}` | `/web#id={id}&model={model}&view_type=form` |
| Domain operator | 支援 `any` | 不支援 `any`（用子查詢替代） |
| 其他 | JSON-RPC 協定相同 | JSON-RPC 協定相同 |

## 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `ODOO_URL` | Odoo 伺服器 URL | `http://localhost:8069` |
| `ODOO_DATABASE` | 資料庫名稱 | - |
| `ODOO_API_KEY` | API Key 認證 | - |
| `READONLY_MODE` | 唯讀模式（禁止寫入操作） | `false` |

建立 `.env` 檔案：

```bash
cp .env.example .env
```

## 安裝

```bash
pip install -r requirements.txt
```

## 啟動方式

### 開發模式（MCP Inspector）

```bash
fastmcp dev inspector odoo_mcp_server.py
```

## 傳輸模式（Transport）

| 模式 | 說明 | 適用情境 |
|------|------|----------|
| `stdio` | 標準輸入輸出（預設） | Claude Desktop、Cursor IDE、本機開發 |
| `http` | HTTP 協定 | 遠端服務、n8n、Web 應用整合 |
| `sse` | Server-Sent Events（已棄用） | 向下相容舊版 Client |

### 啟動不同模式

```bash
# stdio 模式（預設）
python odoo_mcp_server.py

# HTTP 模式
python odoo_mcp_server.py --transport http --host 0.0.0.0 --port 8000

# SSE 模式（已棄用，建議使用 HTTP）
python odoo_mcp_server.py --transport sse --host 0.0.0.0 --port 8000
```

## MCP Resources

| URI | 說明 |
|-----|------|
| `odoo://models` | 列出所有模型 |
| `odoo://model/{model_name}` | 取得模型欄位定義 |
| `odoo://record/{model_name}/{record_id}` | 取得單筆記錄 |
| `odoo://user` | 當前登入用戶資訊 |
| `odoo://company` | 當前用戶所屬公司資訊 |

## MCP Tools

| Tool | 說明 | 唯讀 |
|------|------|------|
| `list_models` | 列出/搜尋可用模型 | Yes |
| `get_fields` | 取得模型欄位定義 | Yes |
| `search_records` | 搜尋記錄 | Yes |
| `count_records` | 計數記錄 | Yes |
| `read_records` | 讀取指定 ID 記錄 | Yes |
| `create_record` | 建立記錄 | No |
| `update_record` | 更新記錄 | No |
| `delete_record` | 刪除記錄（需二次確認） | No |
| `execute_method` | 執行模型方法 | Depends |

## Claude Code MCP 設定

### 本機執行

```sh
claude mcp add odoo-mcp-server -- python odoo_mcp_server.py
```

<details>
<summary><b>手動設定 JSON（加到 <code>~/.claude.json</code>）</b></summary>

```json
{
  "mcpServers": {
    "odoo-mcp-server": {
      "command": "/bin/python",
      "args": [
        "odoo_mcp_server.py"
      ]
    }
  }
}
```

</details>

### Docker

```sh
claude mcp add odoo-mcp-server -- docker run -i --rm --add-host=host.docker.internal:host-gateway -e ODOO_URL=http://host.docker.internal:8069 -e ODOO_DATABASE=odoo18 -e ODOO_API_KEY=your_api_key_here odoo18-mcp-server
```

### 雲端部署（HTTP 模式）

Docker Compose 範例：

```yaml
services:
  odoo-mcp:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ODOO_URL=http://odoo:8069
      - ODOO_DATABASE=odoo18
      - ODOO_API_KEY=your_api_key_here
    command: ["python", "odoo_mcp_server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
    restart: unless-stopped
```

Client 設定：

```sh
claude mcp add --transport http odoo-mcp https://your-cloud-server.com:8000/mcp
```

## Docker 建置

```bash
docker build -t odoo18-mcp-server .
```

## 安全機制

### 唯讀模式

設定 `READONLY_MODE=true` 啟用唯讀模式，適用於生產環境查詢：

- 寫入工具（`create_record`、`update_record`、`delete_record`、`execute_method`）透過 FastMCP tags 直接隱藏，LLM 不會看到這些工具

### 刪除二次確認

`delete_record` 內建 confirm 機制，LLM 必須先以 `confirm=False` 呼叫取得確認提示，經使用者同意後才能以 `confirm=True` 執行刪除。

## 健康檢查

HTTP/SSE transport 模式下提供 `/health` 端點：

```bash
curl http://localhost:8000/health
# {"status": "healthy", "service": "odoo18-mcp-server", "version": "1.0.0"}
```

## 測試

```bash
pip install -r requirements-dev.txt
pytest tests/
```

## Credits

Based on [twtrubiks/odoo19-mcp-server](https://github.com/twtrubiks/odoo19-mcp-server) by [@twtrubiks](https://github.com/twtrubiks).

## License

Apache 2.0
