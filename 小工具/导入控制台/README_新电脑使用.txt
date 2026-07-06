导入控制台（可迁移版）使用说明

一、直接复制整目录
- 把当前「导入控制台」整个文件夹完整复制到新电脑
- 保留 _internal 目录，不要删

二、首次运行
- 双击 导入控制台.exe
- 进入「飞书全量同步」页，确认脚本路径为：
  .\意向学员关系导入到飞书多维表格.py

三、执行飞书全量同步
- 先用 DRY_RUN=true 试跑一次
- 确认无误后改为 DRY_RUN=false 再执行

四、关键配置文件（都在本目录）
- feishu_sync_console_config.json：导入控制台页签配置
- sync_to_feishu.config.json：飞书 app/token/table 配置
- field_mapping.json：写入飞书字段映射

五、注意事项
- 脚本已改为同进程执行，不会再弹出第二个导入控制台窗口
- 若提示缺少 Python，不需要单独装来运行 exe；仅外置脚本手工运行时才需要
