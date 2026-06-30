# 影刀渠道B流程 - 纯 Python 版

这个项目是把影刀流程改成的纯 Python 版本，保留原来的：

- 中文函数名
- 渠道B / 带领B / 渠道A / 带领A 计算逻辑
- 动态晋升合伙宝妈逻辑
- 直属放行逻辑
- 特殊渠道带领指定逻辑
- 原因日志，例如 `[查找渠道B-命中前五]`、`[查找渠道B-直属放行]`
- 飞书业务表并发下载到本地后再计算（保证同批次快照）

## 1. 安装依赖

```bash
pip install requests
```

## 2. 修改配置

打开 `config.py`，重点改：

```python
FEISHU_APP_SECRET = "请填写飞书开放平台里的原始 app_secret"
```

注意：影刀里的 `xbot_visual.decrypt(...)` 是影刀加密串，纯 Python 不能直接用，必须填飞书开放平台里的原始 `App Secret`。

如果你需要运行前自动复制微信数据库，把 `COPY_TASKS` 里的路径打开并改成你的真实路径。

飞书读取流程已改为：

1. 主程序先调用 `download_feishu_tables.py` 脚本下载业务表到 `LOCAL_FEISHU_CACHE_DIR`
2. 从本地缓存 JSON 读取 records
3. 用本地 records 参与后续渠道计算

可在 `config.py` 调整：

```python
LOCAL_FEISHU_CACHE_DIR = r"C:\Users\LENOVO\Desktop\channel_b_飞书缓存"
FEISHU_DOWNLOAD_WORKERS = 6
FEISHU_CACHE_REUSE_MINUTES = 20
```

说明：

- 默认行为：每次运行都会重新下载飞书业务表。
- 仅在显式加 `--use-cache` 时，才会复用本地缓存。
- `FEISHU_CACHE_REUSE_MINUTES` 仅在 `--use-cache` 模式下生效，用于控制缓存复用窗口。

## 3. 运行

```bash
python main.py
```

### 仅下载飞书业务表（不做计算）

如果你只想先把飞书业务表拉到本地缓存，可单独运行：

```powershell
cd "D:\桌面文件\新建文件夹\小工具\channel_b_python\channel_b_python"
& "d:/桌面文件/新建文件夹/.venv/Scripts/python.exe" ".\download_feishu_tables.py"
```

可选参数：

- `--output-dir`：自定义缓存目录
- `--workers`：自定义并发线程数
- `--tables`：只下载指定表名（可传多个）

### 一键开关测试 / 正式模式（推荐）

现在支持命令行参数，不用每次手动改 `config.py`：

- `--test`：测试模式，只保存本地结果，不写回飞书
- `--prod`：正式模式，写回飞书
- 不传参数：沿用 `config.py` 里的 `WRITE_TO_FEISHU`

Windows（PowerShell）示例（推荐在项目目录 `channel_b_python/channel_b_python` 执行）：

```powershell
# 测试模式（仅本地输出）
cd "D:\桌面文件\新建文件夹\小工具\channel_b_python\channel_b_python"
& "d:/桌面文件/新建文件夹/.venv/Scripts/python.exe" ".\main.py" --test

# 测试模式 + 启用飞书缓存复用（会优先复用新鲜缓存）
cd "D:\桌面文件\新建文件夹\小工具\channel_b_python\channel_b_python"
& "d:/桌面文件/新建文件夹/.venv/Scripts/python.exe" ".\main.py" --test --use-cache

# 正式模式（写回飞书）
cd "D:\桌面文件\新建文件夹\小工具\channel_b_python\channel_b_python"
& "d:/桌面文件/新建文件夹/.venv/Scripts/python.exe" ".\main.py" --prod
```

## 4. 输出

### 本地检查模式（默认）

`config.py` 中 `WRITE_TO_FEISHU = False` 时：

- **不会**写回飞书表格
- 结果保存到 `LOCAL_OUTPUT_DIR`（默认 `C:\Users\LENOVO\Desktop\channel_b_检查结果`）
- 重点文件：
  - `内部通讯录_*.csv` — 用 Excel 打开核对渠道 B/A
  - `新增合伙宝妈_*.csv` — 待新增的合伙宝妈
  - `计算统计_*.json` — 数量统计、新增学员
  - `原因日志_*.txt` — 完整调试原因
  - `最新输出索引.json` — 本次输出文件清单

`内部通讯录_*.csv` 已增加稳定排序规则，便于人工核对：

1. 同编号放在一起
2. 同编号下大号（`¿¿¿`）在前
3. 同编号下小号（`!!!`）在后

渠道计算 tab 结果仍会写入：

```text
C:\Users\LENOVO\Desktop\_输出结果_1.txt
```

### 写回飞书模式

确认本地结果无误后，将 `WRITE_TO_FEISHU = True`，再运行 `python main.py`，会写回：

- 汇总通讯录
- 内部通讯录
- 新增合伙宝妈及前五

## 5. 关键文件说明

| 文件                  | 作用                |
| ------------------- | ----------------- |
| `config.py`         | 配置飞书、微信数据库路径、输出路径 |
| `feishu_client.py`  | 飞书多维表格接口封装        |
| `wechat_db.py`      | 读取微信 contact.db   |
| `data_loader.py`    | 读取飞书业务表           |
| `channel_engine.py` | 渠道B / 带领B 核心算法    |
| `output_writer.py`  | 写回飞书              |
| `main.py`           | 入口                |

## 6. 保留的原因日志

运行时会打印类似：

```text
[查找渠道B-入口]
[查找渠道B-轮次]
[查找渠道B-命中前五]
[查找渠道B-直属放行]
[查找渠道B-特殊表命中]
[推荐人缺失]
[动态晋升-初始扫描]
[动态晋升-新增触发]
```

这些日志就是原影刀脚本里的“原因”，方便继续排查为什么某个编号算出某个渠道B。

## 7. 重要提醒

这个版本已经去掉影刀依赖，但飞书字段类型在不同表里可能返回结构不同。如果某个字段读取为空，优先检查 `feishu_client.py` 里的 `字段文本()` 是否需要适配该字段。
