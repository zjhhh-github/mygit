# 意向通讯录 · 用户管理批量上传

外部独立运维工具：把本地用户 JSON/Excel 上传到 **Prospect 意向通讯录 · 用户管理**（`USERS_IMPORT`）。

**默认不会** 操作佣金查询主系统（`http://localhost:8080`）。只有显式加 `--target commission` 才会写主系统。

仅依赖 Python 标准库 + 系统 `curl`，无需 `pip install`。

> 其它 Prospect 脚本见 [../README.md](../README.md)。

## 目录结构

```text
manjike-tools/
├── config.json                         # 统一配置（prospect 段供本工具默认读取）
├── users/
│   ├── upload-users.py                 # 主脚本（默认 Prospect USERS_IMPORT）
│   ├── feishu-users.json               # 飞书导出示例
│   ├── upload-users.config.json
│   └── README.md
```

## 准备工作

路径说明：以下命令在 `manjike-tools/users/` 目录执行。

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
copy upload-users.config.example.json upload-users.config.json
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/users
cp upload-users.config.example.json upload-users.config.json
```

然后：

1. 在上级 `../config.json` 的 **`prospect`** 段填写 `host` / `account` / `password`（本地默认 `http://localhost` + Prospect 管理员账号）。
2. `upload-users.config.json` 里 `host/account/password` 可留空，自动读 `prospect` 段。
3. ⚠️ **禁止** 把 `host` 写成 `http://localhost:8080`（那是佣金主系统，不是意向通讯录）。
4. 把 `--file` 指向你的用户 JSON（如 `./feishu-users.json`）。

## 运行步骤（推荐顺序）

### 第一步：dry-run（不上传）

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
python upload-users.py --config upload-users.config.json --dry-run
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/users
python3 upload-users.py --config upload-users.config.json --dry-run
```

会本地校验 JSON 条数并打印前 3 条样例，不登录、不写库。

### 第二步：实际上传到 Prospect 用户管理

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
python upload-users.py --config upload-users.config.json
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/users
python3 upload-users.py --config upload-users.config.json
```

脚本会：

1. 登录 Prospect → `POST /api/prospect/auth/login`
2. 上传文件 → `POST /api/prospect/import/tasks/upload`（`type=USERS_IMPORT`, `mode=USER_INCREMENTAL`）
3. 轮询 → `GET /api/prospect/import/tasks/{taskId}`
4. 日志写入 `./upload-users.log`

### 第三步：后台验证

打开 **意向通讯录 → 用户管理**：

```text
http://localhost/presale/admin/users
```

或 **导入任务管理** 查看本次任务摘要：

```text
http://localhost/presale/admin/import-tasks
```

成功日志示例：`[IMPORT] 合计 inserted=... updated=... skipped=...`

### 第四步：查看日志

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
Get-Content .\upload-users.log -Tail 50 -Encoding UTF8
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/users
tail -n 50 upload-users.log
```

## 可选：写入佣金主系统（8080）

**仅当你明确需要同步到佣金查询全局用户时** 才使用：

```powershell
cd D:\git\mygit\manjike-tools\users
python upload-users.py --target commission --config upload-users.config.json
```

此时会读 `config.json` 的 **`commission`** 段（`admin` / `admin123`），并调用主系统 `/api/users/batch/import-*`。

## 文件格式说明（重点）

### 飞书导出 JSON（推荐，`feishu-users.json`）

与 Prospect `USERS_IMPORT` 模板一致，使用中文字段名：

```json
[
  {
    "账号": "000024",
    "密码": "j4ogqour",
    "微信号": "Kema88818",
    "称呼": "孙一可",
    "微信原始ID": "labixiaoxin1302411",
    "内部备注": "¿¿¿000024-孙一可",
    "角色": "普通用户"
  }
]
```

也支持 `.xlsx` / `.csv`，表头见 Prospect 后台「用户批量导入」模板下载。

### 1. users.txt（仅 --target commission 或历史兼容）

第一行表头固定 `user_name<TAB>password`，后续每行严格匹配格式：

```text
user_name password
990001-Sample甲 Smpl0001
990002-Sample乙 Smpl0002
990003-Sample丙 Smpl0003
```

字段约束：

| 字段 | 规则 |
|------|------|
| `user_name` | 必须是 `<6位数字>-<显示名>`，如 `990001-张三`（编号在前，破折号分隔） |
| `password` | 必须是 8 位 `[A-Za-z0-9]` 组合，如 `Smpl0001` |
| 角色（可选） | 第 4 列附加 `super_admin` / `commission_admin` / `prospective_admin` / `normal_user`，留空 = 普通用户 |

正则参考（来自后端 `GlobalUserImportTaskService.USERS_TXT_PATTERN`）：

```regex
^(\d{6})-(.*?)\s+([A-Za-z0-9]{8})(?:\s+(\S+))?$
```

### 2. users.json（JSON_B 格式，推荐与 API 交互）

每条记录显式带 `code` 字段（6 位数字），不需要从 user_name 解析：

```json
[
  {
    "code": "990001",
    "username": "990001-Sample甲",
    "password": "Smpl0001",
    "role": "user"
  },
  {
    "code": "990002",
    "username": "990002-Sample乙",
    "password": "Smpl0002",
    "role": "admin"
  }
]
```

字段说明：

| 字段 | 是否必填 | 说明 |
|------|---------|------|
| `code` | 必填 | 6 位数字编号 |
| `username` | 推荐 | `<code>-<显示名>` 形式，会作为 user_name 入库 |
| `password` | 必填 | 8 位 `[A-Za-z0-9]` |
| `role` | 可选 | `user` / `admin` / `super_admin` / `commission_admin` / `prospective_admin`；默认 `user` |
| `wechat` / `internal_remark` / `total_wechat` | 可选 | 后端 sys_user 表对应字段 |

### 3. users.json（JSON_A 格式，用于内部数据迁移）

不带 `code` 字段时为 JSON_A，**必须通过 `internal_remark` 字段提供编号**：

```json
[
  { "internal_remark": "990001-张三", "wechat": "wx_zhang" },
  { "internal_remark": "990002-李四", "wechat": "wx_li" }
]
```

后端用正则 `^(\d{6})-(.+)$` 从 `internal_remark` 抽取编号；
JSON_A 不携带密码，新建用户走后端默认密码逻辑，已存在用户走更新分支。

## 常见问题

### 1. `登录失败` / `403 意向通讯录功能已下线`

- 默认应连 **`http://localhost`**（Prospect），不是 `8080`。
- 账号必须是 **Prospect `prospect_user` 表** 的管理员（见 `config.json` → `prospect`），不是主系统 `admin`。

### 2. `登录失败：HTTP 200 / body={"code": 1001...}`（仅 --target commission）

commission 模式才用主系统 `admin` / `admin123`；默认 prospect 模式勿用 admin 账号。

### 3. 导入成功但后台看不到

请到 **意向通讯录用户管理** 查看：`http://localhost/presale/admin/users`  
不要到佣金主系统「全局用户管理」（`/admin/commission/users`）找。

### 4. `account` 填错 / 登录 401

Prospect 模式用 `config.json` → `prospect.account`（Prospect 管理员手机号账号）。

### 5. `网络错误：[Errno 11001]`

`host` 配置错或 Prospect 未启动。验证 Prospect 登录：

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
curl -X POST "http://localhost/api/prospect/auth/login" -H "Content-Type: application/json" -d "{\"username\":\"你的Prospect账号\",\"password\":\"你的密码\"}"
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/users
curl -X POST "http://localhost/api/prospect/auth/login" -H "Content-Type: application/json" -d '{"username":"你的Prospect账号","password":"你的密码"}'
```

### 6. 中文日志在 PowerShell 里乱码

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\users
chcp 65001
python upload-users.py --config upload-users.config.json
```

**macOS / Linux：** 终端默认 UTF-8，直接进入目录执行即可：

```bash
cd /path/to/manjike-tools/users
python3 upload-users.py --config upload-users.config.json
```

或者直接看 `upload-users.log`（始终是 UTF-8 编码）。

## 安全建议

- `upload-users.config.json` 含明文密码，请：
  - macOS / Linux：`chmod 600 upload-users.config.json`
  - Windows：右键 → 属性 → 安全 → 限制访问权限
- 生产环境强烈建议用 HTTPS，避免 token 在传输中被截获。
- 配置文件不要提交到 Git。

## 退出码

| Code | 含义 |
|------|------|
| 0 | 全部成功 |
| 1 | 登录 / 任务 / 结果失败（含部分用户导入失败） |
| 2 | 参数错误（缺少必填项 / 文件不存在） |
