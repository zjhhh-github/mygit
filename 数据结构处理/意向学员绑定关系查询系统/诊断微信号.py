# -*- coding: utf-8 -*-
"""
诊断单个或多个意向学员微信号在远端 Supabase 中的真实状态，
用来排查"为什么该微信号没被跳过 / 为什么被跳过"。

用法（在当前目录）：
    python 诊断微信号.py onlymeLCFstyle
    python 诊断微信号.py onlymeLCFstyle 其他微信号1 其他微信号2

该脚本复用 增量导入.py 中的 CONFIG 与 get_token()，不改动主流程。
"""
import sys
import importlib.util
from pathlib import Path
from typing import List

import requests


def _load_inc():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("增量导入", here / "增量导入.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main(argv):
    # type: (List[str]) -> int
    if not argv:
        print("用法: python 诊断微信号.py <微信号1> [微信号2 ...]")
        return 2

    INC = _load_inc()
    cfg = INC.CONFIG
    token = INC.get_token()

    base = f"{cfg['SUPABASE_URL']}/rest/v1"
    headers = {
        "apikey": cfg["ANON_KEY"],
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    for raw in argv:
        wx = raw.strip()
        wx_lc = wx.lower()
        print("=" * 60)
        print(f"诊断目标: {wx!r} (lower={wx_lc!r})")

        # 1) students 严格等值查
        r = requests.get(
            f"{base}/students",
            params={"select": "id,wechat_id,enrollment_status", "wechat_id": f"eq.{wx}"},
            headers=headers,
            timeout=cfg["REQUEST_TIMEOUT"],
        )
        print(f"  [students eq {wx!r}] HTTP {r.status_code}")
        strict_rows = r.json() if r.status_code == 200 else []
        print(f"    严格命中 {len(strict_rows)} 行: {strict_rows}")

        # 2) students ilike 模糊查（大小写不敏感）
        r = requests.get(
            f"{base}/students",
            params={"select": "id,wechat_id,enrollment_status", "wechat_id": f"ilike.{wx}"},
            headers=headers,
            timeout=cfg["REQUEST_TIMEOUT"],
        )
        ilike_rows = r.json() if r.status_code == 200 else []
        print(f"  [students ilike {wx!r}] HTTP {r.status_code}，命中 {len(ilike_rows)} 行")
        for row in ilike_rows:
            print(f"    - {row}")

        # 3) 若在 students 找到，查 sources_with_status 的绑定
        all_student_ids = {row.get("id") for row in ilike_rows if row.get("id")}
        if all_student_ids:
            for sid in all_student_ids:
                r = requests.get(
                    f"{base}/sources_with_status",
                    params={
                        "select": "source_wechat_id,bind_date,unbind_date,bind_status",
                        "student_id": f"eq.{sid}",
                    },
                    headers=headers,
                    timeout=cfg["REQUEST_TIMEOUT"],
                )
                src_rows = r.json() if r.status_code == 200 else []
                print(f"  [sources_with_status student_id={sid}] 行数 {len(src_rows)}")
                for s in src_rows:
                    print(f"    - {s}")

        # 4) 作为来源出现在活跃绑定里吗？（按 source_wechat_id 查）
        r = requests.get(
            f"{base}/sources_with_status",
            params={
                "select": "student_id,source_wechat_id,bind_date,unbind_date,bind_status",
                "source_wechat_id": f"ilike.{wx}",
                "bind_status": "eq.有绑定",
            },
            headers=headers,
            timeout=cfg["REQUEST_TIMEOUT"],
        )
        as_src_rows = r.json() if r.status_code == 200 else []
        print(f"  [sources_with_status 作为来源 有绑定 ilike {wx!r}] 行数 {len(as_src_rows)}")
        for s in as_src_rows:
            print(f"    - {s}")

        print()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
