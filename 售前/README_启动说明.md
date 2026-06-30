# 意向学员绑定关系查询系统（阶段1）启动说明

## 1. 已实现内容

- 数据库自动初始化（SQLite）
- 初始管理员自动创建
- 登录接口与令牌鉴权
- 管理员权限校验示例接口
- 绑定规则服务预览接口（解绑日期、绑定状态、来源优先级）

## 2. 初始管理员账号

- 账号：`15648230994`
- 密码：`028056hQ@`

> 说明：首次启动后会在当前目录生成 `intent_binding.db`。

## 3. 启动方式

在 `售前` 目录执行：

```bash
python app.py
```

启动后访问：

- 健康检查：`GET http://127.0.0.1:8000/api/health`

## 4. 核心接口（阶段1）

### 4.1 登录

- `POST /api/auth/login`
- 请求体：

```json
{
  "account": "15648230994",
  "password": "028056hQ@"
}
```

### 4.2 当前用户

- `GET /api/auth/me`
- 请求头：
  - `Authorization: Bearer <token>`

### 4.3 管理员初始化状态

- `GET /api/admin/bootstrap-status`
- 请求头：
  - `Authorization: Bearer <token>`
- 要求：账号角色为 `admin`

### 4.4 规则预览

- `POST /api/rules/preview`
- 请求体示例：

```json
{
  "bindDate": "20260320",
  "bindPeriodDays": 10,
  "defaultBindPeriodDays": 7,
  "sources": [
    {
      "source_wechat_id": "laiyuan001",
      "bind_date": "20250506",
      "bind_period_days": 1
    },
    {
      "source_wechat_id": "laiyuan002",
      "bind_date": "20260320",
      "bind_period_days": 10
    }
  ]
}
```

## 5. 回滚方式

- 停止服务后删除 `intent_binding.db` 即可回到初始状态。
- 新增代码仅在 `售前` 目录，不影响原有脚本工程。
