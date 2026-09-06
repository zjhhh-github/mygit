# -*- coding: utf-8 -*-
"""
自定义插件支持
================

让导入控制台具备「不重新打包 exe，就能新增功能」的能力。

数据模型：
    custom_plugins.json 放在控制台同目录（exe 同目录 / 源码同目录），结构：
        {
            "queue_enabled": true,         # 全局排队总开关默认值（UI 仍可改）
            "plugins": [
                {
                    "id":     "abc12345",            # 唯一 ID，新增时自动生成
                    "name":   "示例：清理临时文件",   # 用户自定义名称（也是卡片标题）
                    "script": "./示例插件.py",        # 外置脚本路径（相对/绝对都可）
                    "entry":  "run",                  # 入口函数名，默认 run
                    "params": {                       # 关键字参数（脚本签名 run(log, **params)）
                        "input_file": "C:/data.json"
                    },
                    "enabled": true,                  # 是否启用（false 仅显示不参与执行）
                    "auto_trigger": {                 # 可选定时
                        "enabled": false,
                        "interval_min": 60,
                        "start_time": ""              # 空 = 启动后等一个间隔
                    }
                }
            ]
        }

插件脚本接口：
    必须暴露一个公共函数（默认名 `run`）：

        def run(log, **params) -> bool:
            log.info("hello")
            return True

    返回 True/False 用来标识"最近一次状态"；什么都不 return 视为成功。

设计要点：
    - 加载用 importlib.spec_from_file_location，每次执行都重新加载，
      和飞书/内部备注/用户上传 Tab 一致——改脚本立刻生效，不用重启 exe。
    - 任何异常都包在 try/except 里，只回写日志，不让 UI 崩溃。
    - 本模块自己不引用任何控制台内部对象，保持低耦合：
      调用方负责把 INC.logger 当作 log 参数注入。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ─────────────────────────────── 数据类 ───────────────────────────────
@dataclass
class AutoTrigger:
    """单个插件的定时配置。"""
    enabled: bool = False
    interval_min: int = 60
    start_time: str = ""  # HH:MM 24h；空 = 启动后等一个间隔


@dataclass
class Plugin:
    """单个插件配置。"""
    id: str
    name: str
    script: str
    entry: str = "run"
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    auto_trigger: AutoTrigger = field(default_factory=AutoTrigger)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:8]


# ─────────────────────────────── 配置 IO ───────────────────────────────
def default_config() -> Dict[str, Any]:
    """配置文件缺省结构。第一次启动会写出这份默认值。"""
    return {
        "queue_enabled": True,
        "plugins": [],
    }


def load_config(path: Path) -> Dict[str, Any]:
    """读取插件配置文件；不存在时写出默认值并返回。

    出错（JSON 解析失败 / 编码异常等）时返回默认值并打印警告，
    绝不阻塞控制台启动。
    """
    if not path.exists():
        try:
            path.write_text(
                json.dumps(default_config(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[扩展功能] 写入默认配置失败：{e}")
        return default_config()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("根节点不是 JSON 对象")
        plugins_raw = raw.get("plugins") or []
        plugins: List[Dict[str, Any]] = []
        for p in plugins_raw:
            if not isinstance(p, dict):
                continue
            # 补齐缺省字段，避免后续 KeyError
            p.setdefault("id", Plugin.new_id())
            p.setdefault("name", "未命名功能")
            p.setdefault("script", "")
            p.setdefault("entry", "run")
            p.setdefault("params", {})
            p.setdefault("enabled", True)
            at = p.get("auto_trigger") or {}
            at.setdefault("enabled", False)
            at.setdefault("interval_min", 60)
            at.setdefault("start_time", "")
            p["auto_trigger"] = at
            plugins.append(p)
        return {
            "queue_enabled": bool(raw.get("queue_enabled", True)),
            "plugins": plugins,
        }
    except Exception as e:
        print(f"[扩展功能] 读取配置失败，使用默认值：{e}")
        return default_config()


def save_config(path: Path, cfg: Dict[str, Any]) -> None:
    """把当前配置写回 JSON。失败仅打印，不抛异常。"""
    try:
        path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[扩展功能] 保存配置失败：{e}")


# ─────────────────────────────── 动态加载 ───────────────────────────────
def load_plugin_module(script_path: str):
    """按路径动态加载插件脚本，复用其入口函数。"""
    target = Path(script_path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"插件脚本不存在：{target}")
    # 用 plugin_<hex> 作为模块名，避免不同插件互相覆盖
    mod_name = f"custom_plugin_{uuid.uuid4().hex[:8]}"
    spec = spec_from_file_location(mod_name, str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件脚本：{target}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_callable(plugin: Dict[str, Any]) -> Callable[[Any], bool]:
    """把一个 plugin 配置打包成一个 (log) -> bool 形式的可调用。

    形式与 task_runner.TaskRunner.submit 的 callable_ 完全一致，
    可以直接丢进队列；也可以直接同步调用 result = make_callable(p)(log)。
    """
    script_path = plugin.get("script", "")
    entry_name = plugin.get("entry") or "run"
    params = dict(plugin.get("params") or {})
    plugin_name = plugin.get("name") or "未命名功能"

    def _run(log) -> bool:
        try:
            mod = load_plugin_module(script_path)
        except Exception as e:
            log.error(f"[扩展功能][{plugin_name}] 加载脚本失败：{e}")
            return False

        fn = getattr(mod, entry_name, None)
        if fn is None or not callable(fn):
            log.error(
                f"[扩展功能][{plugin_name}] 入口函数 {entry_name!r} 不存在或不可调用"
            )
            return False

        try:
            log.info(f"[扩展功能][{plugin_name}] 开始执行")
            rc = fn(log, **params)
            ok = bool(rc) if rc is not None else True
            log.info(
                f"[扩展功能][{plugin_name}] 执行结束 → {'成功' if ok else '失败'}"
            )
            return ok
        except Exception as e:
            log.error(
                f"[扩展功能][{plugin_name}] 执行异常：{e}", exc_info=True
            )
            return False

    return _run
