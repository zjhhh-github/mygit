# 收件人地址查询脚本说明

## 一、脚本用途

`lookup_address.py` 用于根据编号，从飞书多维表格查询「收件人地址」，并输出以下结果：

- 孩子中文全名
- 手机号（对应飞书「地址查询表」里的「收件人手机号」字段）
- 省
- 市
- 区县
- 原地址
- 匹配来源列（`编号` 或 `编号_往月`）

输出会同时打印到终端，并保存为 CSV 文件。

---

## 二、文件位置

- 脚本：`小工具/查询收件人地址/lookup_address.py`
- Windows 快捷启动脚本：`小工具/查询收件人地址/运行查询.bat`
- 默认输出文件：`小工具/查询收件人地址/收件人地址查询结果.csv`

---

## 三、运行前准备

1. 已安装 Python（建议 3.10+，并保证命令行可用 `python` 或 `py`）。
2. 已安装依赖：
   - `requests`
3. 本机可访问飞书开放平台接口。

> 说明：脚本内已内置默认飞书应用配置，也支持通过环境变量覆盖：
>
> - `FEISHU_APP_ID`
> - `FEISHU_APP_SECRET`

---

## 四、参数说明

脚本支持以下参数：

- `--numbers`：直接传多个编号，例如 `--numbers 000487 005022`
- `--input`：从文本文件读取编号（每行一个）
- `--output`：指定输出 CSV 路径

默认行为：

- 如果你不传 `--numbers` 和 `--input`，脚本会自动从飞书「编号来源表」视图的 `编号` 列提取全部编号后查询。

---

## 五、推荐执行方式（从项目根目录）

### 1）先安装依赖

Windows（PowerShell）：

```powershell
cd D:\git\mygit
python -m pip install requests
```

macOS / Linux（bash/zsh）：

```bash
cd /path/to/mygit
python3 -m pip install requests
```

### 2）运行脚本

Windows（PowerShell）：

```powershell
cd D:\git\mygit
python ".\小工具\查询收件人地址\lookup_address.py"
```

macOS / Linux（bash/zsh）：

```bash
cd /path/to/mygit
python3 "./小工具/查询收件人地址/lookup_address.py"
```

---

## 六、可选执行方式（已在脚本目录）

如果你当前目录已经是 `小工具/查询收件人地址`，可以直接执行：

Windows（PowerShell）：

```powershell
python .\lookup_address.py --numbers 000487 005022
```

macOS / Linux（bash/zsh）：

```bash
python3 ./lookup_address.py --numbers 000487 005022
```

读取编号文件并自定义输出文件示例：

Windows（PowerShell）：

```powershell
python .\lookup_address.py --input .\numbers.txt --output .\result.csv
```

macOS / Linux（bash/zsh）：

```bash
python3 ./lookup_address.py --input ./numbers.txt --output ./result.csv
```

---

## 七、Windows 一键运行

脚本目录内已有 `运行查询.bat`，双击即可运行。它会：

1. 切换到脚本目录
2. 调用 `py -3.14 lookup_address.py`
3. 执行完成后暂停窗口

如果你本机没有 `py -3.14`，请改用命令行手动执行 `python lookup_address.py`。

---

## 八、输出结果说明

CSV 列如下：

- `编号`
- `孩子中文全名`
- `手机号`
- `状态`（`已找到` / `未找到`）
- `匹配列`（`编号` / `编号_往月`）
- `省`
- `市`
- `区县`
- `原地址`

终端会显示汇总统计，例如「找到 X / Y，未找到 Z」。

---

## 九、常见问题排查

### 1）提示 `ModuleNotFoundError: No module named 'requests'`

原因：未安装依赖。  
处理：执行 `python -m pip install requests` 后重试。

### 2）提示获取 token 失败

原因：飞书应用配置无效、权限不足或网络不可达。  
处理：

1. 检查 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 是否正确
2. 检查飞书应用是否有对应多维表权限
3. 检查本机网络是否可访问 `https://open.feishu.cn`

### 3）输出全是未找到

原因可能包括：

- 编号格式不对（脚本会规范为 6 位数字）
- 目标视图中数据为空或编号列名不匹配
- 地址查询表未包含对应编号/往月编号

可先用 `--numbers` 传一两个明确存在的编号验证链路。

---

## 十、最小验证步骤

1. 执行脚本（不带参数）：

   - `python lookup_address.py`

2. 查看终端是否出现：

   - 编号来源记录总数
   - 地址查询记录总数
   - 查询结果汇总

3. 打开输出文件：

   - `收件人地址查询结果.csv`

4. 确认至少包含字段：`编号`、`孩子中文全名`、`手机号`、`状态`、`省`、`市`、`区县`、`原地址`

