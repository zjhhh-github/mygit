# -*- coding: utf-8 -*-
"""
意向学员查询系统 —— 模块化子包
================================

本子包是 db_viewer.py 的逐步重构产物，按职责分层：

- config.py      ：路径常量、列索引常量、ProspectiveConfig 配置类
- data_clean.py  ：数据清洗 9 步流水线（纯函数，无 Tk 依赖）
- db_access.py   ：DatabaseReader、contact.db 路径解析、SQLite 连接工厂
- backup.py      ：超微数据库本地/网络备份函数
- service.py     ：ProspectiveQueryService（计划于 Phase 2 引入）
- tasks.py       ：自动刷新 / 自动备份调度器（计划于 Phase 2 引入）
- ui_widgets.py  ：Toast / Loading 等小颗粒 UI 构件（计划于 Phase 2 引入）
- ui_panel.py    ：可嵌入的 ProspectiveQueryPanel（计划于 Phase 2 引入）
- tab_factory.py ：install_prospective_tab(notebook, config)（计划于 Phase 2 引入）

当前阶段（Phase 1）：仅完成无状态部分的搬家，保持 db_viewer.py 的对外行为不变。
"""
