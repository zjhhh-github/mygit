# 飞书多维表格批量下载工具

外部独立运维脚本：把飞书多维表格（Bitable）里配置好的几张表批量下载为本地 CSV 文件，方便离线查看、导入其他系统。

**不会**修改飞书数据、不会写入佣金主系统或意向通讯录系统数据库，纯只读下载。

仅依赖 Python 标准库（`urllib` / `csv` / `json` / `threading` 等），**无需 `pip install`**。

> 其它 `manjike-tools` 脚本见 [../README.md](../README.md)。

## 目录结构

```text
manjike-tools/
└── feishu_download/
    ├── download_feishu_csv.py   # 主脚本：并发下载 + 转 CSV
    ├── config.py                # 表格配置（app_id/secret、下表清单、字段清单）
    ├── output/                  # 下载结果输出目录（CSV 文件）
    └── README.md                # 本文档
```

## 环境要求

- **Python 版本：3.7 及以上**（脚本使用了 `from __future__ import annotations`，3.6 及更早版本会直接报语法错误）。
- 不需要安装任何第三方包。

### ⚠️ 常见环境问题：`python` 命令版本过低

如果你机器上装了多个 Python（例如 Anaconda 自带的 3.6），运行时可能报如下错误：

```text
File "download_feishu_csv.py", line 15
    from __future__ import annotations
    ^
SyntaxError: future feature annotations is not defined
```

**原因**：当前 `python` 命令解析到的是 3.6 及以下版本，不支持该语法。

**排查方式（Windows / PowerShell）：**

```powershell
python --version
py --list
```

**解决方式：** 用 Windows 自带的 `py` 启动器，显式指定一个 3.7+ 的版本运行（下方命令以 3.14 为例，具体版本号请以 `py --list` 输出为准）：

```powershell
cd D:\git\mygit\manjike-tools\feishu_download
py -3.14 download_feishu_csv.py
```

**macOS / Linux：** 通常 `python3` 默认已是较新版本，直接执行即可：

```bash
cd /path/to/manjike-tools/feishu_download
python3 download_feishu_csv.py
```

## 功能概要

1. 读取 `config.py` 中的 `DOWNLOAD_TABLES` 列表，按 `table_id` 使用线程池并发下载多张表（并发单位为「表」）。
2. 每张表内部仍按 `page_token` 顺序分页拉取（每页 500 条，飞书接口上限）。
3. 自动用 `APP_ID` / `APP_SECRET` 获取 `tenant_access_token`，无需手动填 token。
4. 把每条记录的 `fields` 转换为 CSV 可写入的字符串（自动展开飞书的富文本/链接/多选等复杂字段）。
5. 单表下载失败不影响其他表；遇到网络异常或飞书限流（错误码 `1254290`）会在线程内自动重试。
6. 结果保存为 `utf-8-sig` 编码的 CSV，Excel 打开中文不乱码。

## 配置说明（`config.py`）

| 配置项 | 说明 |
|---|---|
| `APP_ID` / `APP_SECRET` | 飞书自建应用凭证。默认写死在代码里，也可用环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 覆盖，优先级更高 |
| `DOWNLOAD_TABLES` | 本次要下载的 `table_id` 列表，按顺序下载；某行前面加 `#` 表示暂不下载该表 |
| `TABLE_MAPPING` | `table_id → {app_token, table_name, fields}` 的映射表；`fields` 为导出的字段清单（严格按此顺序输出），设为 `None` 表示导出该表全部字段 |
| `OUTPUT_DIR` | 输出目录，相对 `feishu_download/` 目录，默认 `output` |
| `PAGE_SIZE` | 单页拉取条数，默认 500（飞书接口上限） |
| `MAX_WORKERS` | 并发下载的表数量，默认 3（表内分页仍顺序执行，不并发） |

当前 `DOWNLOAD_TABLES` 默认下载以下表（均对应飞书「意向通讯录」多维表格 `app_token: Zk05bwki2abD8XsBBOccaFsPn8e`）：

- 意向通讯录（`tblNIWZ1EsDyZ1ug`）
- 内部通讯录（`tblfoJucuZkeL9L1`）
- 合伙宝妈（`tblKa8wryhV4d7F4`）
- 固定渠道B带领B（`tblCYxTgp87U0Y4x`）
- 通用更换带领B（`tblWCSOwwmykQrbp`）
- 个性更换带领B（`tblkdS7RKI9xV4dO`）

「推荐人更换」（`tblzUayWLgynug56`）已在配置中注释，默认不下载。

## 使用步骤

### 第一步：确认待下载的表和字段

打开 `config.py`，检查：

- `DOWNLOAD_TABLES` 里是否包含你需要的表（需要新增就取消对应行的注释，或按 `TABLE_MAPPING` 里的格式补一条新配置）。
- 对应表在 `TABLE_MAPPING` 里的 `fields` 是否是你想要导出的字段（`None` = 导出全部字段）。

### 第二步：运行脚本

**Windows（PowerShell）：**

```powershell
cd D:\git\mygit\manjike-tools\feishu_download
py -3.14 download_feishu_csv.py
```

**macOS / Linux：**

```bash
cd /path/to/manjike-tools/feishu_download
python3 download_feishu_csv.py
```

### 第三步：验证结果

- 终端最后会打印下载汇总，形如：

```text
下载完成:
成功:
6个
失败:
0个
总耗时:
3.2秒
明细:
  ✓ 意向通讯录.csv（字段数:7，数据量:1234条）
  ...
```

- 打开 `D:\git\mygit\manjike-tools\feishu_download\output\` 目录，检查对应表名的 `.csv` 文件是否已生成/更新（可看文件修改时间、行数是否符合预期）。
- 若某张表下载失败，汇总的「明细」里会显示 `✗ table_id (表名)：错误原因`，可根据错误信息判断是网络问题、`app_token`/`table_id` 配置错误，还是权限不足。

## 输出说明

- 输出目录：`feishu_download/output/`（脚本会自动创建，不存在时无需手动建）。
- 文件命名：`{table_name}.csv`（`table_name` 取自 `config.py` 中 `TABLE_MAPPING` 的配置）。
- 编码：`utf-8-sig`，Windows Excel 双击打开中文正常显示。
- 若某个配置的字段在飞书表里实际不存在，日志会打印「字段不存在」的字段名清单，CSV 中该列仍会输出但内容为空。

## 常见问题

### 1. `SyntaxError: future feature annotations is not defined`

见上方「环境要求」章节，本质是 Python 版本过低（<3.7），换用 `py -3.14`（或任意 3.7+ 版本）运行即可。

### 2. 报错 `TABLE_MAPPING 中未配置 table_id「xxx」`

`DOWNLOAD_TABLES` 里写了一个 `table_id`，但 `TABLE_MAPPING` 里没有对应配置。检查两处的 `table_id` 是否一致（复制粘贴时容易多/少字符）。

### 3. 报错 `飞书接口业务失败：code=xxx`

一般是 `APP_ID` / `APP_SECRET` 权限不足，或 `app_token` / `table_id` 填错、对应表已被删除/改动了权限。可以先确认能否在飞书网页版正常打开该多维表格。

### 4. 一直等待/限流

日志会打印 `[限流] 飞书返回 1254290 TooManyRequest`，脚本会自动按 2/5/10 秒等待重试，无需手动处理；如果频繁出现，可以调小 `config.py` 里的 `MAX_WORKERS`。

## 安全建议

- `config.py` 中的 `APP_SECRET` 目前是明文写在代码里的默认值，**不要**把包含真实密钥的 `config.py` 提交到公共仓库；如需团队共享，建议改用环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 传入，代码里只保留占位默认值。
- `output/` 目录下的 CSV 可能包含用户微信号、姓名等个人信息，注意不要随意外发。
