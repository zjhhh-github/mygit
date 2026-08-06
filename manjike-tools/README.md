# manjike-tools

运维与数据同步脚本集合，适配 **Prospect 独立部署**（意向通讯录）与 **主系统**（用户批量导入）。

## 环境说明

| 系统 | 默认入口 | 用途 |
|---|---|---|
| **Prospect 独立系统** | `http://localhost` | 内部通讯录、意向学员导入/导出 |
| **主系统 backend** | `http://localhost:8080` | 用户批量导入（`users/`） |

⚠️ **禁止** 对 Prospect 接口使用 `http://localhost:8080`，会返回：

```json
{"code":403,"message":"意向通讯录功能已下线"}
```

直连 Prospect 后端（不经 Nginx）时使用：`http://localhost:18081`

---

## 统一配置

> **路径说明**：以下命令在 `manjike-tools/` 根目录执行。

复制模板并填写账号：

Windows（PowerShell）：

```powershell
cd D:\git\mygit\manjike-tools
copy config.example.json config.json
```

macOS / Linux：

```bash
cd /path/to/manjike-tools
cp config.example.json config.json
```

`config.json` 结构：

```json
{
  "prospect": {
    "host": "http://localhost",
    "account": "你的Prospect账号",
    "password": "你的密码"
  },
  "commission": {
    "host": "http://localhost:8080",
    "account": "admin",
    "password": "admin123"
  }
}
```

> `users/upload-users.py` **默认读 `prospect` 段**；只有 `--target commission` 才读 `commission` 段。

**优先级**（Prospect）：

1. 环境变量 `PROSPECT_HOST` / `PROSPECT_ACCOUNT` / `PROSPECT_PASSWORD`
2. 各脚本 `*.config.json` 的「服务端」段
3. 根目录 `config.json` 的 `prospect` 段
4. 默认 `http://localhost`

各模块脚本里的 `host/account/password` 留空时，自动读 `config.json`。

---

## 目录结构

```
manjike-tools/
├── config.json              # 统一配置（Prospect + 主系统）
├── config.example.json
├── common.py                  # HTTP、登录、配置解析
├── prospect_client.py         # Prospect 导入任务、轮询、curl 上传
├── internal/                  # 内部通讯录（导出 + 上传）
│   ├── export_internal_contacts.py
│   └── upload_internal.py
├── prospect/                  # 意向学员（导出、上传、飞书同步）
│   ├── export_prospect_students.py
│   ├── xlsx_to_prospect_json.py
│   ├── upload_prospect.py
│   └── 同步意向学员到飞书.py
└── users/                     # 主系统用户批量导入
    └── upload-users.py
```

---

## 公共模块

### `common.py`

- `login()` — 主系统 `/api/auth/login`
- `login_prospect()` — Prospect `/api/prospect/auth/login`，返回 `{token, userInfo}`
- `http_get()` / `http_post()` — 带 Bearer Token
- `http_upload_multipart()` — curl `-F` 文件上传（兼容 Spring Boot）
- `resolve_prospect_config()` / `resolve_commission_config()`

### `prospect_client.py`

- `upload_import_file()` — 上传文件 + 轮询至 SUCCESS/FAILED
- `poll_import_task()` — 查询 `GET /api/prospect/import/tasks/{taskId}`
- `normalize_contact_mode()` / `normalize_student_mode()`

---

## 常用命令

以下命令均从**项目根目录** `manjike-tools/` 进入对应子目录后执行。Windows 用 PowerShell；macOS / Linux 把路径换成你的实际 clone 路径即可。

### 内部通讯录

路径说明：脚本位于 `manjike-tools/internal/`。

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\internal

# 1. 从 contact.db 导出 JSON
python export_internal_contacts.py

# 2. 上传到 Prospect（INTERNAL_IMPORT）
python upload_internal.py

# 仅校验，不调接口
python upload_internal.py --dry-run
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/internal

python3 export_internal_contacts.py
python3 upload_internal.py
python3 upload_internal.py --dry-run
```

**验证**：第一次 `inserted=11469`；第二次增量 `skipped=11469`。

### 意向学员

路径说明：脚本位于 `manjike-tools/prospect/`。

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\prospect

# 从 xlsx 备份生成意向通讯录.json（推荐人嵌套格式）
python xlsx_to_prospect_json.py

# 或从 Prospect 后端导出（同样输出「推荐人」嵌套格式）
python export_prospect_students.py

python upload_prospect.py
python upload_prospect.py --dry-run

# 主文件：意向通讯录.json（嵌套「推荐人」数组；upload_prospect.py 上传前会自动转为后端「来源」格式）
# export 脚本默认输出：意向学员数据导出.json（结构相同，文件名可在 export_prospect.config.json 改）
# 时间戳备份：logs/ 下 CSV（扁平化，Excel 可开）

# 导出后同步到飞书（需先配置 sync_to_feishu.config.json，默认读 意向通讯录.json）
python 同步意向学员到飞书.py
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/prospect

python3 xlsx_to_prospect_json.py
python3 export_prospect_students.py
python3 upload_prospect.py
python3 upload_prospect.py --dry-run
python3 同步意向学员到飞书.py
```

### 意向通讯录 · 用户管理（Prospect USERS_IMPORT，默认）

路径说明：脚本位于 `manjike-tools/users/`。详见 [users/README.md](users/README.md)。

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
python upload-users.py --config upload-users.config.json --dry-run
python upload-users.py --config upload-users.config.json
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/users
python3 upload-users.py --config upload-users.config.json --dry-run
python3 upload-users.py --config upload-users.config.json
```

验证页：`http://localhost/presale/admin/users`

### 佣金主系统全局用户（8080，需显式指定）

```powershell
cd D:\git\mygit\manjike-tools\users
python upload-users.py --target commission --config upload-users.config.json
```

### 环境变量覆盖（可选）

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\internal
$env:PROSPECT_HOST = "http://localhost"
python upload_internal.py
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/internal
export PROSPECT_HOST=http://localhost
python3 upload_internal.py
```

---

## 导入任务 API（Prospect）

| 操作 | 方法 | 路径 |
|---|---|---|
| 上传文件创建任务 | POST | `/api/prospect/import/tasks/upload` |
| 查询进度 | GET | `/api/prospect/import/tasks/{taskId}` |

multipart 参数：

- `file` — `.json` / `.xlsx` / `.csv`
- `type` — `INTERNAL_IMPORT` / `STUDENTS_IMPORT` / `USERS_IMPORT`
- `mode` — 内部通讯录：`CONTACT_INCREMENTAL` / `CONTACT_OVERWRITE`；学员：`FULL` / `INCREMENTAL` / `APPEND`

---

## 依赖

- Python 3.6+
- 系统 `curl`（文件上传必需，Windows 10+ 自带）
- 仅标准库（`users/`、`prospect/同步意向学员到飞书.py` 除外，后者需 `requests`）

---

## 故障排查

| 现象 | 处理 |
|---|---|
| `意向通讯录功能已下线` | 把 `config.json` → `prospect.host` 改为 `http://localhost` |
| `type 只能是 STUDENTS_IMPORT...` | 确保使用 curl 上传（已内置），勿用手工 urllib multipart |
| Prospect 登录 401 | 使用 `prospect_user` 表账号，不是主系统 `admin` |
| 上传超时 | 调大 `max_wait_seconds`（全量约 9000 条建议 ≥3600） |
