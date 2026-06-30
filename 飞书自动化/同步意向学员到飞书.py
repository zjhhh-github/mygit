# -*- coding: utf-8 -*-
"""
转发到 manjike-tools/prospect 版同步脚本（已移除秒哒/Supabase 相关逻辑）。

兼容两种调用方式：
1) 作为脚本独立执行（python 同步意向学员到飞书.py）
2) 被导入控制台动态加载后，调用 export_students()/sync_to_feishu()

说明：
- 这里的 export_students/sync_to_feishu 是“兼容桥接层”，真实工作仍由目标脚本完成。
- 这样无需修改导入控制台旧调用逻辑，也能兼容当前转发结构。
"""
import subprocess
import sys
import os
from pathlib import Path

# 导入控制台会在运行时覆盖这两个全局变量；脚本独立运行时使用默认值
DRY_RUN = True
log = print


def _emit(msg: str) -> None:
    """统一日志输出，兼容控制台注入的 log 回调。"""
    try:
        if callable(log):
            log(msg)
            return
    except Exception:
        pass
    print(msg)


def _resolve_target() -> Path:
    """
    自动向上搜索目标主脚本，兼容源码目录、dist 目录等不同运行位置。
    """
    script_name = "同步意向学员到飞书.py"
    cur = Path(__file__).resolve()
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add_candidate(p: Path) -> None:
        key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(p)

    # 0) 环境变量优先（可显式指定主脚本目录）
    # 例如：set MANJIKE_TOOLS_PROSPECT_DIR=<导入控制台同目录>\manjike-tools\prospect
    env_dir = (os.environ.get("MANJIKE_TOOLS_PROSPECT_DIR") or "").strip()
    if env_dir:
        _add_candidate(Path(env_dir) / script_name)

    # 1) 与导入控制台同目录的常见相对路径（不依赖固定盘符）
    _add_candidate(cur.parent / "manjike-tools" / "prospect" / script_name)
    _add_candidate(cur.parent / "prospect" / script_name)

    # 2) 原有策略：从当前脚本路径向上查找
    for base in [cur.parent, *cur.parents]:
        _add_candidate(base / "manjike-tools" / "prospect" / script_name)

    # 3) 常见工作目录：当前进程工作目录及其祖先
    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        _add_candidate(base / "manjike-tools" / "prospect" / script_name)

    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "未找到主脚本 manjike-tools/prospect/同步意向学员到飞书.py，已搜索：\n"
        + "\n".join(str(x) for x in candidates)
    )


def _run_target(extra_args: list[str] | None = None) -> int:
    """调用目标主脚本；DRY_RUN=True 时优先传 --dry-run，不支持时自动回退重试。"""
    target = _resolve_target()
    args = list(extra_args or [])

    if DRY_RUN:
        cmd = [sys.executable, str(target), "--dry-run", *args]
        rc = subprocess.call(cmd)
        if rc == 0:
            return 0
        _emit("[飞书同步] 目标脚本可能不支持 --dry-run，改为无参数重试。")

    cmd = [sys.executable, str(target), *args]
    return subprocess.call(cmd)


def export_students():
    """
    兼容导入控制台旧接口：当前转发模式不在本地导出数据，返回占位列表即可。
    实际导出与同步都在 sync_to_feishu 中交给目标脚本执行。
    """
    _emit("[飞书同步] 转发脚本模式：export_students 由目标脚本接管。")
    return []


def sync_to_feishu(_students):
    """
    兼容导入控制台旧接口：执行目标脚本并返回导入控制台期望的 summary 结构。
    """
    rc = _run_target()
    if rc != 0:
        raise RuntimeError(f"目标脚本执行失败，exit_code={rc}")
    return {
        "deleted": 0,
        "exported": 0,
        "prepared": 0,
        "inserted": 0,
        "failed": 0,
    }


if __name__ == "__main__":
    raise SystemExit(_run_target(sys.argv[1:]))
