# 慧分账自动下载脚本

基于 Selenium + Chrome 的生产级自动化脚本，自动登录 [hbsk PaaS 支付订单页](https://paas.hbsk.com/#/share/finance/payorder)，按「昨天 ~ 今天」查询并下载。

---

## 一、项目结构

```
慧分账下载/
├─ main.py                 # 入口 & 全局配置（URL / 日期 / 目录 / HEADLESS）
├─ requirements.txt        # 依赖清单
├─ cookies.pkl             # 首次登录后自动生成（免登录用）
├─ logs/                   # 日志 + 关键步骤截图 + 错误截图
│   └─ runtime.log
├─ downloads/              # 下载文件保存目录
└─ utils/
    ├─ __init__.py
    ├─ logger.py           # 日志封装（控制台 + 文件 + 滚动）
    ├─ browser.py          # Chrome 启动 / 反检测 / 自动下载配置
    ├─ cookies.py          # cookie 持久化（pickle）
    ├─ downloader.py       # 下载完成检测（轮询 .crdownload）
    └─ page_actions.py     # 日期注入 / 点击查询 / 点击下载 / 截图
```

---

## 二、模块说明

| 模块 | 职责 |
|------|------|
| `main.py` | 全局配置、主流程编排、异常兜底、浏览器关闭 |
| `utils/logger.py` | 统一日志格式，写入 `logs/runtime.log`（5MB×5 滚动） |
| `utils/browser.py` | 用 `webdriver-manager` 自动装驱动；反检测 + 隐藏 `navigator.webdriver`；CDP 强制下载目录 |
| `utils/cookies.py` | `save_cookies` / `load_cookies`，免登录复用 |
| `utils/downloader.py` | 拍快照 + 轮询，检测 `.crdownload` 消失 + 新文件出现 |
| `utils/page_actions.py` | JS 注入设置 ElementUI 日期（触发 `input` / `change` / `blur`），多策略点击「查询」「下载」 |

---

## 三、安装

> 需要 Python 3.11+ 与本机已安装 Chrome 浏览器。

Windows（PowerShell），从项目根目录执行：

```powershell
cd "D:\桌面文件\新建文件夹\慧分账下载"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux：

```bash
cd ~/慧分账下载
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 四、运行

从项目根目录执行：

```powershell
python main.py
```

首次运行流程：

1. 自动打开 Chrome 并跳转到目标页面。
2. 控制台提示「请在浏览器中手动完成登录」，最多等 300 秒。
3. 登录成功后自动保存 `cookies.pkl`。
4. 自动设置「开始日期 = 昨天，结束日期 = 今天」。
5. 自动点击「查询」→ 等待表格 → 点击「下载」。
6. 检测到下载完成后输出：

```
[SUCCESS] 下载完成：D:\...\downloads\xxx.xlsx
```

第二次运行：检测到 `cookies.pkl` 后会尝试自动登录，若 cookie 已过期则回退到手动登录。

---

## 五、配置项（`main.py` 顶部）

| 变量 | 含义 | 默认值 |
|------|------|--------|
| `URL` | 目标页面 | hbsk 支付订单页 |
| `HEADLESS` | 是否无头 | `False`（首次必须 False，便于登录） |
| `START_DATE` / `END_DATE` | 查询日期 | 自动 = 昨天 / 今天 |
| `DOWNLOAD_DIR` | 下载目录 | `./downloads` |
| `LOGIN_WAIT_TIMEOUT` | 等待人工登录秒数 | 300 |
| `DOWNLOAD_TIMEOUT` | 等待下载完成秒数 | 300 |

---

## 六、打包 EXE（可选）

```powershell
pip install pyinstaller
pyinstaller -F -n huifenzhang --add-data "utils;utils" main.py
```

打包说明：

- `-F`：单文件
- `-n huifenzhang`：可执行文件名
- `--add-data "utils;utils"`：把 `utils` 包打进 EXE（macOS / Linux 用 `:` 替代 `;`）

打包完成后：

```powershell
.\dist\huifenzhang.exe
```

> 注意：首次运行 EXE 仍会通过 `webdriver-manager` 联网下载匹配版本的 chromedriver；离线环境请提前缓存。

---

## 七、日志与截图

- 运行日志：`logs/runtime.log`
- 关键步骤截图：`logs/after_login_*.png` / `after_set_date_*.png` / `after_query_*.png` / `after_download_*.png`
- 异常截图：`logs/error_*.png`

---

## 八、常见问题

1. **没找到日期输入框 / 查询按钮**：网站改版后 selector 失效，修改 `utils/page_actions.py` 中的 `DATE_INPUT_XPATHS` / `QUERY_BUTTON_LOCATORS` / `DOWNLOAD_BUTTON_LOCATORS`。
2. **Cookie 失效**：删除 `cookies.pkl` 后重新运行手动登录即可。
3. **下载超时**：调大 `DOWNLOAD_TIMEOUT`，或检查目标站点是否真的弹了下载。
