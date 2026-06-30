# -*- coding: utf-8 -*-
"""
导入控制台同目录的飞书同步桥接脚本。

用途：
1) 作为导入控制台动态加载脚本（提供 export_students/sync_to_feishu）
2) 作为独立脚本执行（python 同步意向学员到飞书.py）

说明：
- 真实实现位于 manjike-tools/prospect/同步意向学员到飞书.py
- 本文件只做“同目录调用入口 + 兼容接口”，便于控制台稳定调用
"""
import os
import subprocess
import sys
from pathlib import Path

DRY_RUN = True
log = print


def _emit(msg: str) -> None:
    try:
        if callable(log):
            log(msg)
            return
    except Exception:
        pass
    print(msg)


def _resolve_target() -> Path:
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

    # 可选：允许用户手工覆盖
    env_dir = (os.environ.get("MANJIKE_TOOLS_PROSPECT_DIR") or "").strip()
    if env_dir:
        _add_candidate(Path(env_dir) / script_name)

    # 当前脚本目录及其祖先中查找 manjike-tools/prospect
    for base in [cur.parent, *cur.parents]:
        _add_candidate(base / "manjike-tools" / "prospect" / script_name)

    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "未找到主脚本 manjike-tools/prospect/同步意向学员到飞书.py，已搜索：\n"
        + "\n".join(str(x) for x in candidates)
    )


def _run_target(extra_args: list[str] | None = None) -> int:
    target = _resolve_target()
    args = list(extra_args or [])
    if DRY_RUN:
        cmd = [sys.executable, str(target), "--dry-run", *args]
        rc = subprocess.call(cmd)
        if rc == 0:
            return 0
        _emit("[飞书同步] 目标脚本可能不支持 --dry-run，改为无参数重试。")
    return subprocess.call([sys.executable, str(target), *args])


def export_students():
    _emit("[飞书同步] 转发脚本模式：export_students 由目标脚本接管。")
    return []


def sync_to_feishu(_students):
    rc = _run_target()
    if rc != 0:
        raise RuntimeError(f"目标脚本执行失败，exit_code={rc}")
    return {"deleted": 0, "exported": 0, "prepared": 0, "inserted": 0, "failed": 0}


if __name__ == "__main__":
    raise SystemExit(_run_target(sys.argv[1:]))

