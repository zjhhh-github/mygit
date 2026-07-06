# 导入控制台（启动说明）

## 结论

该工具目录已迁移到：`D:\git\mygit\小工具\导入控制台`。  
请在这个目录中执行脚本。

## 目录与文件

- 脚本：`上传用户结构.py`
- 配置：`user_upload_console_config.json`
- 输出：`上传用户结构.json`
- 缓存：`upload_password_cache.json`

## 运行方式（Windows PowerShell）

### 方式一（推荐）：从项目根目录进入后执行

```powershell
cd D:\git\mygit\小工具\导入控制台
python .\上传用户结构.py
```

### 常用参数

```powershell
# 只生成 JSON，不上传
python .\上传用户结构.py --no-upload

# 只上传本地已有 JSON
python .\上传用户结构.py --upload-only

# 干跑（不真正发请求）
python .\上传用户结构.py --dry-run
```

## 验证方式

- 执行后应看到脚本打印阶段日志与汇总信息。
- 目录下应生成/更新 `上传用户结构.json` 与 `upload_password_cache.json`。
