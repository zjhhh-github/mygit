# manjike 用户批量上传工具

外部独立运维工具：通过 manjike 后端 HTTP API 批量导入用户。
脱离 manjike 项目源码，可拷贝到任意能访问后端的机器使用。

仅依赖 Python 标准库，无需 `pip install`。

## 目录结构

```text
manjike-tools/
├─ upload-users.py                       主脚本
├─ upload-users.config.example.json      配置模板（复制成 upload-users.config.json 用）
├─ users-sample.txt                      数据样例
└─ README.md                             本文件
```

## 准备工作

1. 把 `upload-users.config.example.json` 复制为 `upload-users.config.json`，按需修改。
2. 把 `users-sample.txt` 替换成你的真实用户列表文件，或通过 `--file` 指定。
3. 确保运行机能访问后端（`host` 字段对应的地址）。

## 运行步骤（推荐顺序）

### 第一步：dry-run 预览（不写库）

```powershell
# Windows
python upload-users.py --config upload-users.config.json --dry-run
```

```bash
# macOS / Linux
python3 upload-users.py --config upload-users.config.json --dry-run
```

输出会显示后端解析到的 `total` 和将要执行的创建 / 更新数量。
确认数量符合预期后，再做下一步。

### 第二步：实际导入

```powershell
python upload-users.py --config upload-users.config.json
```

脚本会：

1. 登录后端 → 拿 JWT token
2. 调用 `POST /api/users/batch/import-async` 提交异步任务
3. 每秒轮询 `GET /api/users/batch/import-tasks/{taskId}`
4. 任务进入终态后调用 `/result` 拿成功 / 失败明细
5. 写完整日志到 `upload-users.log`

### 第三步：查看日志

`upload-users.log` 含所有请求摘要 + 错误明细 JSON。

## 文件格式说明（重点）

> **核心约束：每个用户必须有一个 6 位数字编号 `user_code`**（项目内部工号体系）。
> 不带编号或编号不是 6 位数字 → 后端会以 "编号格式错误" 跳过该行。

### 1. users.txt（推荐运维批量同步）

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

### 1. `登录失败：HTTP 200 / body={"code": 2000, "message": "编号不能为空"}`

`account` 字段填错了。后端登录接口接收 `code`（user_code）或 `username`（user_name），脚本会同时塞这两个字段。
请确保 `account` 是后端 `sys_user` 表里的 `user_code` **或** `user_name`。

### 2. `403 仅超级管理员可执行此操作`

登录账号必须是超级管理员（super_admin），普通用户 / 售前管理员都不行。

### 3. `401 未登录，请先登录`

token 异常或后端实际没拿到 token。
检查脚本 `login()` 函数解析的 token 字段（默认按 `data.token` / `data.access_token` 兼容）。

### 4. `网络错误：[Errno 11001]`

`host` 配置错或防火墙拦了，先用 `curl` 或浏览器直接访问 `{host}/api/auth/login` 验证连通性。

### 5. 中文日志在 PowerShell 里乱码

```powershell
chcp 65001
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
