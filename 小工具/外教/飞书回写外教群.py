# -*- coding: utf-8 -*-
r"""
独立小工具：读取飞书多维表格「编号」列，用「¿¿¿编号」去微信联系人库精确查询
对应的唯一外教群名，并把结果回写到飞书表格的「是否有外教群」「外教群名」两列；
同时顺带给「开始材料」「充值课时」两列补默认值。

【对应的飞书多维表格】
    https://ipcjg02m9k.feishu.cn/base/Zk05bwki2abD8XsBBOccaFsPn8e?table=tbljyq4wU1jONHGC&view=vewWwgXYys
    app_token = Zk05bwki2abD8XsBBOccaFsPn8e
    table_id  = tbljyq4wU1jONHGC
    view_id   = vewWwgXYys（只拉取这个视图下的记录，视图的筛选/排序会生效）

【为什么用「¿¿¿编号」而不是「孩子中文全名」搜索】
    微信内部专用备注格式固定为「¿¿¿编号-孩子中文全名」（例如 ¿¿¿005411-亓书琦），
    「编号」在飞书表里是全表唯一的学员编号。直接按姓名搜索容易因为重名
    （比如多个学员都叫"依诺"）搜出好几个不相关的人，导致外教群判断不唯一；
    改成「¿¿¿ + 编号」去搜，就能精确匹配到唯一的一条微信备注，基本不会再重名。

【判定规则】
    1. 用「¿¿¿ + 编号」去本地微信 contact.db 中搜索联系人
       （复用 查询外教群.py 的搜索与外教群判定逻辑：群名含大写 CT 或 XXJ）。
    2. 汇总所有命中联系人的外教群名（可能同时有 CT 群和 XXJ 群）。
       如果飞书「外教平台」列已登记具体平台（CT 或 XXJ），
       就只看「属于该平台」的群，其余平台的群直接忽略、不参与判断：
       - 属于该平台的群 0 个（没有外教群）：
           - 「外教平台」不为空 → 是否有外教群 = 否，外教群名 = 孩子中文全名 + " " + 外教平台
           - 「外教平台」为空   → 是否有外教群 = 否，外教群名 = ""（不返回外教群名）
       - 属于该平台的群唯一 1 个（能唯一确定）    → 是否有外教群 = 是，外教群名 = 该唯一群名
       - 属于该平台的群 2 个及以上（无法唯一确定，需要人工核实）→ 是否有外教群 = 不唯一，
         外教群名 = 属于该平台的所有群名（用「、」拼接，全部列出）
    3. 把上面两个结果写回飞书对应记录的「是否有外教群」「外教群名」字段。
       注：「是否有外教群」在飞书里是单选字段，若「不唯一」这个选项尚不存在，
       飞书接口会在写入时自动新建该选项（官方文档明确支持，无需手动改字段）。
    4. 「开始材料」列：目前只有一个选项「WOW L 系列 001」。
       只在「该字段当前为空」「本次算出的外教群名不为空」且
       「是否有外教群 = 否」时才补默认值：
       - 「是否有外教群 = 是」，说明已经唯一确定外教群，不再需要补默认值；
       - 「是否有外教群 = 不唯一」，说明查到多个外教群、需要人工核实，也不补默认值；
       已经手动填过的记录不会被覆盖；如果外教群名本身是空的
       （没有外教群、且外教平台也没登记），开始材料也保持为空，不强行填默认值。
    5. 「充值课时」列：数字字段，只在「是否有外教群 = 否」且该字段当前为空时，
       补默认值 144（判定为「是」/「不唯一」都不补）；已经手动填过的记录不会被覆盖。

【安全机制】
    默认是「预览模式」（不会真正写入飞书），只在控制台打印将要写入的内容；
    确认无误后加 --apply 参数才会真正调用飞书接口写入，避免误改线上数据。

【依赖】
    - 仅使用 Python 标准库（urllib / sqlite3 等），无需 pip install。
    - 同目录下的 查询外教群.py（本脚本会直接 import 复用其中的联系人搜索、
      外教群索引构建逻辑，不重复造轮子，也不会修改该文件）。
    - 需要能访问本机的微信 contact.db（默认路径见 查询外教群.py 里的说明）。

【关于自动拷贝数据库】
    每次运行都会先自动从微信同步盘拷贝最新的「内部专用」「内部专用2」两个数据库到
    本地（复用 查询外教群.py 的拷贝逻辑），保证外教群判断用的是最新数据，无需手动
    先跑一遍 查询外教群.py --copy-db。如果本机连不上同步盘，或想跳过这一步直接用
    本地已有的数据库文件，加 --skip-copy-db 参数即可。

【用法示例】
    # 预览模式：只打印计算结果，不写入飞书（会先自动拷贝最新数据库，强烈建议先跑一次预览确认结果）
    python 飞书回写外教群.py

    # 预览模式 + 只处理前 5 条，方便快速验证逻辑是否正确
    python 飞书回写外教群.py --limit 5

    # 确认结果无误后，真正写入飞书
    python 飞书回写外教群.py --apply

    # 自定义微信数据库路径（多个用分号分隔）
    python 飞书回写外教群.py --db "C:\a.db;C:\b.db" --apply

    # 跳过自动拷贝数据库这一步，直接用本地已有的数据库文件
    python 飞书回写外教群.py --skip-copy-db --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 让本脚本可以直接 import 同目录下的 查询外教群.py，复用其中的联系人搜索、
# 外教群索引构建逻辑（不修改该文件，只读取里面的函数）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import 查询外教群 as 外教群工具  # noqa: E402


# ============================================================
# 一、飞书多维表格定位信息 + 应用凭证
# ============================================================

# 飞书自建应用凭证：与 manjike-tools/feishu_download 共用同一应用
# 可用环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 覆盖，优先级更高
APP_ID: str = os.environ.get("FEISHU_APP_ID", "cli_a96f36ed1538dbcf")
APP_SECRET: str = os.environ.get("FEISHU_APP_SECRET", "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB")

# 目标多维表格：意向通讯录 -> 外教群相关登记表
FEISHU_HOST = "https://open.feishu.cn"
APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
TABLE_ID = "tbljyq4wU1jONHGC"
# 只读取这个视图里的记录（视图里如果设置了筛选/排序，拉取结果会按视图的筛选结果来）
VIEW_ID = "vewWwgXYys"

# 飞书表格里涉及的字段名（必须与飞书后台字段名完全一致）
字段_孩子中文全名 = "孩子中文全名"
字段_编号 = "编号"
字段_外教平台 = "外教平台"
字段_是否有外教群 = "是否有外教群"
字段_外教群名 = "外教群名"
字段_开始材料 = "开始材料"
字段_充值课时 = "充值课时"

# 「开始材料」单选列目前只有这一个选项，默认值统一填这个；
# 只在该字段当前为空时才写入，已经手动填过的记录不会被覆盖。
开始材料默认值 = "WOW L 系列 001"

# 「充值课时」是数字字段，只在「是否有外教群=否」且该字段当前为空时才补这个默认值，
# 已经手动填过的记录不会被覆盖（与「开始材料」保护逻辑一致）。
充值课时默认值 = 144

# 微信内部专用备注固定格式为「¿¿¿编号-孩子中文全名」，用这个前缀 + 编号去搜索，
# 可以精确匹配到唯一联系人，避免按姓名搜索时因为重名导致匹配到多个不相关的人。
编号搜索前缀 = "¿¿¿"

PAGE_SIZE = 500

# 限流错误码：TooManyRequest，按 2/5/10 秒等待后重试（与 feishu_download 保持一致）
FEISHU_RATE_LIMIT_CODE = 1254290
RATE_LIMIT_WAIT_SECONDS = [2, 5, 10]
MAX_RETRY = 3


# ============================================================
# 二、飞书 HTTP 接口封装（仅标准库，无第三方依赖）
# ============================================================

def 确保控制台UTF8输出() -> None:
    """Windows 下强制 stdout/stderr 使用 utf-8，避免中文乱码。"""
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """发送 JSON HTTP 请求并返回解析后的 dict（不含重试逻辑）。"""
    data_bytes: Optional[bytes] = None
    req_headers: Dict[str, str] = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    req = Request(url=url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} 调用失败：{url}\n响应体：{err_body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络错误：{url}\n原因：{exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"响应不是合法 JSON：{raw[:500]}") from exc


def request_with_retry(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """带重试的飞书请求：网络异常最多重试 MAX_RETRY 次；限流按 2/5/10 秒等待。"""
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = http_json(method, url, headers=headers, body=body)
            code = resp.get("code")
            if code == FEISHU_RATE_LIMIT_CODE:
                wait_idx = min(attempt - 1, len(RATE_LIMIT_WAIT_SECONDS) - 1)
                wait_sec = RATE_LIMIT_WAIT_SECONDS[wait_idx]
                print(f"  [限流] 飞书返回 TooManyRequest，第 {attempt} 次重试，等待 {wait_sec} 秒 ...")
                time.sleep(wait_sec)
                continue
            if code != 0:
                raise RuntimeError(f"飞书接口业务失败：code={code}, msg={resp.get('msg')}")
            return resp
        except RuntimeError as exc:
            last_error = exc
            if attempt < MAX_RETRY:
                print(f"  [重试] 第 {attempt} 次失败：{exc}，即将重试 ...")
                time.sleep(1.0 * attempt)
                continue
    raise RuntimeError(f"请求失败（已重试 {MAX_RETRY} 次）：{last_error}") from last_error


def 获取租户访问令牌() -> str:
    """获取 tenant_access_token（应用维度访问令牌）。"""
    url = FEISHU_HOST + "/open-apis/auth/v3/tenant_access_token/internal"
    resp = request_with_retry("POST", url, body={"app_id": APP_ID, "app_secret": APP_SECRET})
    token = resp.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"响应缺少 tenant_access_token：{resp}")
    return str(token)


def 拉取全部记录(token: str) -> List[Dict[str, Any]]:
    """
    分页拉取飞书多维表格里「VIEW_ID 指定视图」下的全部记录。
    只传 view_id 不会改变返回的字段结构，只是记录范围/顺序按该视图的
    筛选、排序规则来（视图里如果设置了筛选条件，拉取结果也只包含筛选后的记录）。
    """
    base_url = FEISHU_HOST + f"/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}"}

    全部记录: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    页码 = 0

    while True:
        页码 += 1
        params: Dict[str, Any] = {"page_size": PAGE_SIZE}
        if VIEW_ID:
            params["view_id"] = VIEW_ID
        if page_token:
            params["page_token"] = page_token
        url = base_url + "?" + urlencode(params)

        resp = request_with_retry("GET", url, headers=headers)
        data = resp.get("data") or {}
        本页记录 = data.get("items") or []
        全部记录.extend(本页记录)
        print(f"  [拉取] 第 {页码} 页 +{len(本页记录)} 条，累计 {len(全部记录)} 条")

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break

    return 全部记录


def 更新单条记录(
    token: str,
    record_id: str,
    是否有外教群: str,
    外教群名: str,
    开始材料: Optional[str] = None,
    充值课时: Optional[int] = None,
) -> None:
    """
    把结果写回飞书指定记录：
      - 「是否有外教群」「外教群名」两个字段每次都会写入；
      - 「开始材料」「充值课时」都只在传入非 None 时才写入
        （由调用方决定：仅当该字段原本为空、且满足对应补默认值条件时才传值，
        避免覆盖已有数据）。
    """
    url = FEISHU_HOST + f"/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{record_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    写入字段: Dict[str, Any] = {
        字段_是否有外教群: 是否有外教群,
        字段_外教群名: 外教群名,
    }
    if 开始材料 is not None:
        写入字段[字段_开始材料] = 开始材料
    if 充值课时 is not None:
        写入字段[字段_充值课时] = 充值课时

    body = {"fields": 写入字段}
    request_with_retry("PUT", url, headers=headers, body=body)


def 提取字段文本(字段值: Any) -> str:
    """把飞书字段值统一转为字符串（兼容纯文本 / 富文本数组 / 单选等结构）。"""
    if 字段值 is None:
        return ""
    if isinstance(字段值, str):
        return 字段值.strip()
    if isinstance(字段值, (int, float)):
        return str(字段值)
    if isinstance(字段值, list):
        parts: List[str] = []
        for item in 字段值:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            elif item is not None:
                parts.append(str(item).strip())
        return "".join(parts).strip()
    if isinstance(字段值, dict):
        return str(字段值.get("text", "")).strip()
    return str(字段值).strip()


# ============================================================
# 三、核心业务逻辑：¿¿¿编号 → 是否有外教群 / 外教群名
# ============================================================

def 查询某个编号对应的外教群集合(
    编号: str,
    db路径列表: List[str],
    外教群索引: Dict[str, List[str]],
) -> set:
    """
    用「¿¿¿ + 编号」在微信联系人库中搜索（精确匹配微信备注的固定格式
    「¿¿¿编号-孩子中文全名」），汇总所有命中联系人的外教群名，
    返回去重后的群名集合（可能为空、1 个或多个）。

    若编号为空，直接返回空集合（不发起搜索）。
    """
    if not 编号:
        return set()

    搜索关键词 = f"{编号搜索前缀}{编号}"
    命中联系人列表 = 外教群工具.跨库搜索联系人(db路径列表, 搜索关键词)

    群名集合: set = set()
    for 联系人 in 命中联系人列表:
        wxid = (联系人.get("username") or "").strip()
        for 群名 in (外教群索引.get(wxid) or []):
            群名集合.add(群名)
    return 群名集合


# 「是否有外教群」三种结果对应的取值
结果_否 = "否"       # 一个外教群都没查到
结果_是 = "是"       # 查到唯一一个外教群，可以直接确定
结果_不唯一 = "不唯一"  # 查到 2 个及以上不同外教群，无法唯一确定，需要人工核实


def _群名属于该外教平台(群名: str, 外教平台: str) -> bool:
    """判断某个外教群名，是否属于飞书「外教平台」单选列登记的那个平台（CT / XXJ）。"""
    return 外教平台 in 群名


def 计算是否有外教群与群名(
    孩子中文全名: str,
    编号: str,
    外教平台: str,
    db路径列表: List[str],
    外教群索引: Dict[str, List[str]],
) -> Tuple[str, str]:
    """
    核心判定规则（搜索用「¿¿¿+编号」，兜底群名仍用「孩子中文全名+外教平台」）：

    第一步：用编号搜到的所有外教群，可能同时包含 CT 群和 XXJ 群
        （比如孩子同时在两个平台都上过课）。
    第二步：如果飞书「外教平台」列已经登记了具体平台（CT 或 XXJ），
        就只看「属于该平台」的那些群，其余平台的群直接忽略，不参与判断：
        - 属于该平台的群 0 个（没有外教群）：
            - 「外教平台」不为空 → (否, 孩子中文全名 + " " + 外教平台)
            - 「外教平台」为空   → (否, "")（外教平台都没填，不返回外教群名）
        - 属于该平台的群唯一 1 个（能唯一确定）       → (是, 该唯一群名)
        - 属于该平台的群 2 个及以上（无法唯一确定）    → (不唯一, 这些群名用「、」拼接)

    举例：某学员搜到「Sw彭思予 CT」和「彭思予Siri ... XXJ」两个群，
    但飞书「外教平台」登记的是 XXJ，那么只看 XXJ 群 —— 只有 1 个 XXJ 群，
    判定为 (是, 彭思予Siri ... XXJ)；CT 群会被忽略，不影响结果。
    """
    全部群名集合 = 查询某个编号对应的外教群集合(编号, db路径列表, 外教群索引)

    # 「外教平台」已登记具体平台时，只保留属于该平台的群；未登记时维持原样（不过滤）
    if 外教平台:
        目标群名集合 = {群名 for 群名 in 全部群名集合 if _群名属于该外教平台(群名, 外教平台)}
    else:
        目标群名集合 = 全部群名集合

    if len(目标群名集合) == 0:
        if not 外教平台:
            return 结果_否, ""
        return 结果_否, f"{孩子中文全名} {外教平台}"

    if len(目标群名集合) == 1:
        唯一群名 = next(iter(目标群名集合))
        return 结果_是, 唯一群名

    全部群名 = "、".join(sorted(目标群名集合))
    return 结果_不唯一, 全部群名


# ============================================================
# 四、主流程
# ============================================================

def 解析命令行参数() -> argparse.Namespace:
    解析器 = argparse.ArgumentParser(
        description="读取飞书多维表格「编号」列，用「¿¿¿编号」精确查询唯一外教群名并回写飞书",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python 飞书回写外教群.py                  （预览模式，不写入飞书；默认会先拷贝最新数据库）
  python 飞书回写外教群.py --limit 5        （只预览前 5 条，快速验证）
  python 飞书回写外教群.py --apply          （确认无误后真正写入飞书）
  python 飞书回写外教群.py --db "C:\\a.db;C:\\b.db" --apply
  python 飞书回写外教群.py --skip-copy-db --apply  （跳过拷贝，直接用本地已有数据库）
        """,
    )
    解析器.add_argument(
        "--db",
        type=str,
        default=None,
        metavar="路径",
        help="自定义微信 contact.db 路径，多个用分号分隔（不填则使用 查询外教群.py 里的默认路径）",
    )
    解析器.add_argument(
        "--skip-copy-db",
        action="store_true",
        help="跳过「拷贝内部专用数据库」这一步，直接用本地已有的数据库文件（默认每次运行都会先拷贝最新的）",
    )
    解析器.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="条数",
        help="只处理前 N 条记录，便于先小范围验证效果（默认 0 表示处理全部记录）",
    )
    解析器.add_argument(
        "--apply",
        action="store_true",
        help="真正写入飞书。不加此参数时为预览模式，只打印结果、不修改飞书数据",
    )
    return 解析器.parse_args()


def main() -> int:
    确保控制台UTF8输出()
    参数 = 解析命令行参数()

    db路径列表 = 外教群工具.解析数据库路径列表(参数.db)
    模式说明 = "【真正写入飞书】" if 参数.apply else "【预览模式，不会写入飞书】"

    print("=" * 60)
    print("飞书「外教群」批量查询回写工具")
    print(模式说明)
    if not 参数.skip_copy_db:
        print("正在拷贝内部专用数据库 ...")
        for 结果 in 外教群工具.拷贝内部专用数据库():
            print(f"  {结果}")
    print(f"目标表格：app_token={APP_TOKEN}, table_id={TABLE_ID}, view_id={VIEW_ID}")
    print(f"微信联系人数据库：{len(db路径列表)} 个")
    for 路径 in db路径列表:
        print(f"  - {路径}")
    print("=" * 60)

    print("\n[步骤1/4] 获取飞书访问令牌 ...")
    token = 获取租户访问令牌()
    print("  成功。")

    print("\n[步骤2/4] 拉取飞书表格全部记录 ...")
    全部记录 = 拉取全部记录(token)
    print(f"  共 {len(全部记录)} 条记录。")

    if 参数.limit > 0:
        全部记录 = 全部记录[: 参数.limit]
        print(f"  [--limit] 仅处理前 {len(全部记录)} 条。")

    print("\n[步骤3/4] 构建外教群成员索引（微信内部专用库）...")
    外教群索引 = 外教群工具.构建外教群成员索引(db路径列表)
    print(f"  索引覆盖 {len(外教群索引)} 个微信ID。")

    print("\n[步骤4/4] 逐条计算并回写 ...")
    print("-" * 60)

    # 同一个编号只会对应一条记录，但仍做个缓存兜底，避免异常情况下重复搜索微信库
    结果缓存: Dict[Tuple[str, str, str], Tuple[str, str]] = {}

    成功数 = 0
    跳过数 = 0
    失败数 = 0
    是的数量 = 0
    否的数量 = 0
    不唯一数量 = 0
    开始材料补默认值数量 = 0
    充值课时补默认值数量 = 0

    for 序号, 记录 in enumerate(全部记录, start=1):
        record_id = 记录.get("record_id") or 记录.get("id")
        字段值 = 记录.get("fields") or {}

        孩子中文全名 = 提取字段文本(字段值.get(字段_孩子中文全名))
        编号 = 提取字段文本(字段值.get(字段_编号))
        外教平台 = 提取字段文本(字段值.get(字段_外教平台))
        开始材料现有值 = 提取字段文本(字段值.get(字段_开始材料))
        充值课时现有值 = 提取字段文本(字段值.get(字段_充值课时))

        if not 孩子中文全名:
            跳过数 += 1
            print(f"[{序号}/{len(全部记录)}] [跳过] record_id={record_id}：「孩子中文全名」为空")
            continue

        if not 编号:
            print(f"[{序号}/{len(全部记录)}] [提示] {孩子中文全名}：「编号」为空，无法用编号搜索，按「没有外教群」处理")

        缓存key = (编号, 孩子中文全名, 外教平台)
        if 缓存key in 结果缓存:
            是否有外教群, 外教群名 = 结果缓存[缓存key]
        else:
            是否有外教群, 外教群名 = 计算是否有外教群与群名(孩子中文全名, 编号, 外教平台, db路径列表, 外教群索引)
            结果缓存[缓存key] = (是否有外教群, 外教群名)

        if 是否有外教群 == 结果_是:
            是的数量 += 1
        elif 是否有外教群 == 结果_不唯一:
            不唯一数量 += 1
        else:
            否的数量 += 1

        # 「开始材料」只在「当前为空」「外教群名不为空」且「是否有外教群=否」时才补默认值：
        # 已经手动填过的不覆盖；
        # 「是否有外教群=是」说明已经唯一确定外教群，流程已经在走，不需要再补；
        # 「是否有外教群=不唯一」说明查到多个外教群、需要人工核实，不应该自动帮它定默认值，也不补；
        # 查不到外教群名（没有外教群 + 外教平台也没登记）时，开始材料保持为空，不强行填默认值。
        待写入开始材料: Optional[str] = None
        if not 开始材料现有值 and 外教群名 and 是否有外教群 == 结果_否:
            待写入开始材料 = 开始材料默认值
            开始材料补默认值数量 += 1

        # 「充值课时」只在「是否有外教群=否」且该字段当前为空时才补默认值 144，
        # 已经手动填过的记录不会被覆盖（判定为「是」/「不唯一」都不补）。
        待写入充值课时: Optional[int] = None
        if not 充值课时现有值 and 是否有外教群 == 结果_否:
            待写入充值课时 = 充值课时默认值
            充值课时补默认值数量 += 1

        材料提示 = f"，开始材料→{待写入开始材料}" if 待写入开始材料 else ""
        课时提示 = f"，充值课时→{待写入充值课时}" if 待写入充值课时 is not None else ""
        print(
            f"[{序号}/{len(全部记录)}] {孩子中文全名}（编号={编号 or '空'}） → "
            f"是否有外教群={是否有外教群}，外教群名={外教群名}{材料提示}{课时提示}"
        )

        if 参数.apply:
            try:
                更新单条记录(token, record_id, 是否有外教群, 外教群名, 待写入开始材料, 待写入充值课时)
                成功数 += 1
            except Exception as ex:
                失败数 += 1
                print(f"    [失败] 写入飞书出错：{ex}", file=sys.stderr)

    print("-" * 60)
    print("=" * 60)
    print("处理完成。")
    print(f"总记录数：{len(全部记录)}｜跳过（姓名为空）：{跳过数}")
    print(f"判定为「是」（唯一确定外教群）：{是的数量} 条")
    print(f"判定为「否」（没有外教群）：{否的数量} 条")
    print(f"判定为「不唯一」（查到多个不同外教群，需要人工核实）：{不唯一数量} 条")
    print(f"「开始材料」为空、将补默认值「{开始材料默认值}」：{开始材料补默认值数量} 条")
    print(f"「充值课时」为空、将补默认值「{充值课时默认值}」：{充值课时补默认值数量} 条")
    if 参数.apply:
        print(f"实际写入飞书：成功 {成功数} 条，失败 {失败数} 条")
    else:
        print("当前为预览模式，未写入飞书。确认结果无误后，请加 --apply 参数正式写入。")
    print("=" * 60)

    return 0 if 失败数 == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[已取消]")
        sys.exit(130)
