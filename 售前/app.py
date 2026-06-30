"""
意向学员绑定关系查询系统 - 后端最小可运行版本（阶段1）

说明：
1. 本文件实现基础能力：数据表初始化、初始管理员、登录鉴权、绑定规则服务。
2. 采用 Flask + SQLite，便于本地快速启动与回滚。
3. 当前仅提供阶段1所需接口，后续可按需求文档继续扩展。
"""

import json
import os
import sqlite3
import threading
import traceback
import uuid
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, List, Optional

from flask import Flask, Response, g, jsonify, request, send_from_directory
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


# ------------------------------
# 应用基础配置
# ------------------------------
APP_NAME = "意向学员绑定关系查询系统"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "intent_binding.db")

# 可通过环境变量覆盖，便于不同环境部署
SECRET_KEY = os.getenv("INTENT_BINDING_SECRET", "intent-binding-dev-secret")
TOKEN_EXPIRE_SECONDS = int(os.getenv("INTENT_BINDING_TOKEN_EXPIRE_SECONDS", "43200"))  # 默认12小时

INIT_ADMIN_ACCOUNT = "15648230994"
INIT_ADMIN_PASSWORD = "028056hQ@"
INIT_ADMIN_ROLE = "admin"

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_MAP_TO_VALUE = {
    "admin": ROLE_ADMIN,
    "user": ROLE_USER,
    "管理员": ROLE_ADMIN,
    "普通用户": ROLE_USER,
}
ROLE_MAP_TO_LABEL = {
    ROLE_ADMIN: "管理员",
    ROLE_USER: "普通用户",
}

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["SECRET_KEY"] = SECRET_KEY
serializer = URLSafeTimedSerializer(SECRET_KEY)

# 上传目录：用于阶段2“申请提交”接口存储截图
UPLOAD_ROOT = os.path.join(BASE_DIR, "uploads")
SOURCE_APPLICATION_UPLOAD_DIR = os.path.join(UPLOAD_ROOT, "source_applications")
os.makedirs(SOURCE_APPLICATION_UPLOAD_DIR, exist_ok=True)
# 导入任务锁：保护任务状态更新，避免并发写覆盖
IMPORT_JOB_LOCK = threading.Lock()


# ------------------------------
# 数据库连接与初始化
# ------------------------------
def get_db() -> sqlite3.Connection:
    """获取当前请求上下文的数据库连接。"""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc: Optional[BaseException]) -> None:
    """请求结束后关闭数据库连接。"""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_database() -> None:
    """初始化系统数据表与初始管理员账号。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()

        # 用户表：账号、密码哈希、角色
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_plain TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # 兼容迁移：旧版本 users 表没有 password_plain 字段时自动补齐
        user_columns = cursor.execute("PRAGMA table_info(users)").fetchall()
        user_column_names = {row[1] for row in user_columns}
        if "password_plain" not in user_column_names:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN password_plain TEXT NOT NULL DEFAULT ''"
            )

        # 意向学员主表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wechat_id TEXT NOT NULL UNIQUE,
                is_enrolled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # 兼容迁移：旧版本 prospects 表没有 is_enrolled 字段时自动补齐
        prospect_columns = cursor.execute("PRAGMA table_info(prospects)").fetchall()
        prospect_column_names = {row[1] for row in prospect_columns}
        if "is_enrolled" not in prospect_column_names:
            cursor.execute(
                "ALTER TABLE prospects ADD COLUMN is_enrolled INTEGER NOT NULL DEFAULT 0"
            )

        # 来源记录表（解绑日期/绑定状态为计算字段，不持久化）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prospect_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL,
                source_wechat_id TEXT NOT NULL,
                bind_date TEXT NOT NULL,
                bind_period_days INTEGER,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(prospect_id) REFERENCES prospects(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_prospect_sources_prospect_id
            ON prospect_sources(prospect_id)
            """
        )

        # 添加来源申请表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS source_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL,
                source_wechat_id TEXT NOT NULL,
                apply_date TEXT NOT NULL,
                bind_period_days INTEGER,
                prospect_wechat_screenshot_url TEXT NOT NULL,
                chat_screenshots_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
                reviewed_by TEXT,
                reviewed_at TEXT,
                review_remark TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(prospect_id) REFERENCES prospects(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_applications_prospect_id
            ON source_applications(prospect_id)
            """
        )

        # 系统配置表（用于默认绑定周期等）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS system_configs (
                config_key TEXT PRIMARY KEY,
                config_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # 导入任务表：用于异步导入进度持久化，刷新页面后可继续查看
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS import_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                progress_percent INTEGER NOT NULL DEFAULT 0,
                total_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                payload_json TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )

        # 初始化管理员：仅在不存在时创建，避免覆盖已有数据
        now = current_time_str()
        cursor.execute("SELECT id FROM users WHERE account = ?", (INIT_ADMIN_ACCOUNT,))
        existed_admin = cursor.fetchone()
        if existed_admin is None:
            cursor.execute(
                """
                INSERT INTO users (account, password_hash, password_plain, role, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    INIT_ADMIN_ACCOUNT,
                    generate_password_hash(INIT_ADMIN_PASSWORD),
                    INIT_ADMIN_PASSWORD,
                    INIT_ADMIN_ROLE,
                    now,
                    now,
                ),
            )

        # 若管理员账号已存在但明文密码字段为空，补齐初始化密码用于后台展示/导出
        cursor.execute(
            """
            UPDATE users
            SET password_plain = ?, updated_at = ?
            WHERE account = ? AND (password_plain IS NULL OR password_plain = '')
            """,
            (INIT_ADMIN_PASSWORD, now, INIT_ADMIN_ACCOUNT),
        )

        conn.commit()
    finally:
        conn.close()


# ------------------------------
# 绑定规则服务（统一业务规则入口）
# ------------------------------
class BindingRuleService:
    """绑定规则服务：统一处理解绑日期、绑定状态、来源优先级。"""

    @staticmethod
    def parse_yyyymmdd(date_str: str) -> datetime:
        """将 yyyyMMdd 字符串解析为 datetime（仅日期语义）。"""
        return datetime.strptime(date_str, "%Y%m%d")

    @staticmethod
    def format_yyyymmdd(date_obj: datetime) -> str:
        """将 datetime 格式化为 yyyyMMdd 字符串。"""
        return date_obj.strftime("%Y%m%d")

    @classmethod
    def calc_unbind_date(cls, bind_date: str, bind_period_days: int) -> str:
        """计算解绑日期：绑定日期 + 绑定周期(天)。"""
        bind_dt = cls.parse_yyyymmdd(bind_date)
        unbind_dt = bind_dt + timedelta(days=bind_period_days)
        return cls.format_yyyymmdd(unbind_dt)

    @classmethod
    def calc_bind_status(cls, bind_date: str, bind_period_days: int, today: Optional[str] = None) -> str:
        """
        计算绑定状态。
        规则：
        - 解绑日期 < 今天 -> 无绑定
        - 解绑日期 >= 今天 -> 有绑定
        """
        unbind_date = cls.calc_unbind_date(bind_date, bind_period_days)
        today_str = today or datetime.now().strftime("%Y%m%d")
        return "有绑定" if unbind_date >= today_str else "无绑定"

    @classmethod
    def pick_display_source(cls, sources: List[Dict[str, Any]], default_period_days: int) -> Optional[Dict[str, Any]]:
        """
        根据优先级选择应展示的来源记录。
        规则：
        1. 无来源 -> None
        2. 有“有绑定”记录 -> 取绑定日期最新的一条
        3. 无“有绑定”记录 -> 取解绑日期最近（最大）的一条
        """
        if not sources:
            return None

        normalized: List[Dict[str, Any]] = []
        for item in sources:
            period = item.get("bind_period_days")
            if period is None:
                period = default_period_days

            # 关键业务逻辑：统一补全计算字段，保证前后端展示一致
            bind_date = item["bind_date"]
            unbind_date = cls.calc_unbind_date(bind_date, int(period))
            status = cls.calc_bind_status(bind_date, int(period))

            normalized.append(
                {
                    **item,
                    "bind_period_days": int(period),
                    "unbind_date": unbind_date,
                    "bind_status": status,
                }
            )

        active_sources = [x for x in normalized if x["bind_status"] == "有绑定"]
        if active_sources:
            # 有绑定优先，按绑定日期最新
            return sorted(active_sources, key=lambda x: x["bind_date"], reverse=True)[0]

        # 全部无绑定时，取解绑日期最近的一条
        return sorted(normalized, key=lambda x: x["unbind_date"], reverse=True)[0]


# ------------------------------
# 鉴权与通用工具
# ------------------------------
def current_time_str() -> str:
    """返回当前时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_date_yyyymmdd() -> str:
    """返回当天日期，格式 yyyyMMdd。"""
    return datetime.now().strftime("%Y%m%d")


def normalize_enrolled(value: Any) -> Optional[int]:
    """
    将报名状态标准化为 0/1。
    兼容输入：0/1、true/false、已报名/未报名。
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    mapping = {
        "1": 1,
        "true": 1,
        "yes": 1,
        "已报名": 1,
        "0": 0,
        "false": 0,
        "no": 0,
        "未报名": 0,
    }
    if text in mapping:
        return mapping[text]
    return None


def enrolled_label(value: int) -> str:
    """报名状态中文标签。"""
    return "已报名" if int(value) == 1 else "未报名"


def parse_bool_query_arg(value: Any) -> bool:
    """解析 URL 查询参数中的布尔值。"""
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def normalize_role(role_raw: Any) -> Optional[str]:
    """将角色值标准化为内部值：admin/user。"""
    value = str(role_raw).strip() if role_raw is not None else ""
    if not value:
        return None
    return ROLE_MAP_TO_VALUE.get(value)


def role_label(role_value: str) -> str:
    """将内部角色值转为中文标签。"""
    return ROLE_MAP_TO_LABEL.get(role_value, role_value)


def build_user_view(row: sqlite3.Row) -> Dict[str, Any]:
    """统一构建用户返回结构，避免各接口字段不一致。"""
    return {
        "id": row["id"],
        "account": row["account"],
        "password": row["password_plain"],
        "role": row["role"],
        "roleLabel": role_label(row["role"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def get_default_bind_period_days(db: sqlite3.Connection) -> Optional[int]:
    """读取系统默认绑定周期（天），未配置时返回 None。"""
    row = db.execute(
        "SELECT config_value FROM system_configs WHERE config_key = ?",
        ("default_bind_period_days",),
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["config_value"])
    except (TypeError, ValueError):
        return None


def update_import_job(job_id: str, fields: Dict[str, Any]) -> None:
    """
    更新导入任务状态。
    说明：使用独立连接，确保后台线程可安全更新任务进度。
    """
    if not fields:
        return

    allowed = {
        "status",
        "progress_percent",
        "total_count",
        "processed_count",
        "success_count",
        "failed_count",
        "error_message",
        "updated_at",
        "finished_at",
    }
    parts: List[str] = []
    params: List[Any] = []
    for key, value in fields.items():
        if key in allowed:
            parts.append("{} = ?".format(key))
            params.append(value)
    if not parts:
        return

    with IMPORT_JOB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                "UPDATE import_jobs SET {} WHERE job_id = ?".format(", ".join(parts)),
                tuple(params + [job_id]),
            )
            conn.commit()
        finally:
            conn.close()


def process_import_job(job_id: str) -> None:
    """
    后台执行意向学员导入任务。
    说明：该函数在独立线程中执行，支持进度百分比与失败信息回写。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT payload_json, created_by FROM import_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return

        now = current_time_str()
        update_import_job(
            job_id,
            {
                "status": "running",
                "updated_at": now,
                "error_message": None,
            },
        )

        payload = json.loads(row["payload_json"])
        if not isinstance(payload, list) or not payload:
            update_import_job(
                job_id,
                {
                    "status": "failed",
                    "error_message": "JSON 导入格式错误，应为非空数组",
                    "updated_at": current_time_str(),
                    "finished_at": current_time_str(),
                },
            )
            return

        total_count = len(payload)
        update_import_job(job_id, {"total_count": total_count, "updated_at": current_time_str()})

        default_period = get_default_bind_period_days(conn)
        success_count = 0
        failed_count = 0
        processed_count = 0
        input_wechat_set = set()

        for index, item in enumerate(payload, start=1):
            try:
                if not isinstance(item, dict):
                    raise ValueError("第{}条不是对象结构".format(index))

                wechat_id = str(item.get("意向学员微信号", item.get("wechatId", ""))).strip()
                is_enrolled_raw = item.get("是否报名", item.get("isEnrolled", 0))
                is_enrolled = normalize_enrolled(is_enrolled_raw)
                if is_enrolled is None:
                    raise ValueError("第{}条是否报名不合法".format(index))

                if not wechat_id:
                    raise ValueError("第{}条意向学员微信号为空".format(index))
                if wechat_id in input_wechat_set:
                    raise ValueError("第{}条意向学员微信号重复".format(index))
                input_wechat_set.add(wechat_id)

                existed = conn.execute(
                    "SELECT id FROM prospects WHERE wechat_id = ?",
                    (wechat_id,),
                ).fetchone()
                if existed is not None:
                    raise ValueError("第{}条意向学员微信号已存在".format(index))

                sources_raw = item.get("来源", item.get("sources", []))
                if sources_raw is None:
                    sources_raw = []
                if not isinstance(sources_raw, list):
                    raise ValueError("第{}条来源字段必须为数组".format(index))

                now_local = current_time_str()
                cursor = conn.execute(
                    """
                    INSERT INTO prospects (wechat_id, is_enrolled, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (wechat_id, is_enrolled, now_local, now_local),
                )
                prospect_id = cursor.lastrowid

                for s_idx, source in enumerate(sources_raw, start=1):
                    if not isinstance(source, dict):
                        raise ValueError("第{}条第{}个来源不是对象结构".format(index, s_idx))

                    source_wechat_id = str(
                        source.get("来源微信号", source.get("sourceWechatId", ""))
                    ).strip()
                    bind_date = str(source.get("绑定日期", source.get("bindDate", ""))).strip()
                    period_raw = source.get("绑定周期", source.get("bindPeriodDays"))

                    if not source_wechat_id:
                        raise ValueError("第{}条第{}个来源微信号为空".format(index, s_idx))
                    if not bind_date or not validate_yyyymmdd(bind_date):
                        raise ValueError("第{}条第{}个绑定日期格式错误".format(index, s_idx))

                    period_value = None
                    if period_raw is not None and str(period_raw).strip() != "":
                        period_value = int(period_raw)
                        if period_value <= 0:
                            raise ValueError("第{}条第{}个绑定周期必须为正整数".format(index, s_idx))
                    elif default_period is None:
                        raise ValueError(
                            "第{}条第{}个来源缺少绑定周期且系统默认绑定周期未配置".format(
                                index, s_idx
                            )
                        )

                    conn.execute(
                        """
                        INSERT INTO prospect_sources (
                            prospect_id, source_wechat_id, bind_date, bind_period_days,
                            created_by, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prospect_id,
                            source_wechat_id,
                            bind_date,
                            period_value,
                            row["created_by"],
                            now_local,
                            now_local,
                        ),
                    )

                conn.commit()
                success_count += 1
            except Exception as exc:
                conn.rollback()
                failed_count += 1
                # 失败项不终止整个任务，持续处理下一条，提高导入吞吐与容错
                update_import_job(job_id, {"error_message": str(exc), "updated_at": current_time_str()})

            processed_count += 1
            progress = int((processed_count / float(total_count)) * 100) if total_count else 100
            update_import_job(
                job_id,
                {
                    "progress_percent": progress,
                    "processed_count": processed_count,
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "updated_at": current_time_str(),
                },
            )

        final_status = "completed" if failed_count == 0 else "completed_with_errors"
        update_import_job(
            job_id,
            {
                "status": final_status,
                "progress_percent": 100,
                "updated_at": current_time_str(),
                "finished_at": current_time_str(),
            },
        )
    except Exception:
        update_import_job(
            job_id,
            {
                "status": "failed",
                "error_message": traceback.format_exc(limit=1),
                "updated_at": current_time_str(),
                "finished_at": current_time_str(),
            },
        )
    finally:
        conn.close()


def build_display_result(
    prospect_wechat_id: str, is_enrolled: int, selected_source: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    组装查询结果数据。
    当没有来源时，来源相关字段按需求返回为空。
    """
    if not selected_source:
        return {
            "prospectWechatId": prospect_wechat_id,
            "isEnrolled": int(is_enrolled),
            "isEnrolledLabel": enrolled_label(int(is_enrolled)),
            "sourceWechatId": "",
            "bindDate": "",
            "bindPeriodDays": None,
            "unbindDate": "",
            "bindStatus": "",
            "canApply": int(is_enrolled) == 0,  # 空状态且未报名时允许申请
        }

    status = selected_source["bind_status"]
    return {
        "prospectWechatId": prospect_wechat_id,
        "isEnrolled": int(is_enrolled),
        "isEnrolledLabel": enrolled_label(int(is_enrolled)),
        "sourceWechatId": selected_source["source_wechat_id"],
        "bindDate": selected_source["bind_date"],
        "bindPeriodDays": selected_source["bind_period_days"],
        "unbindDate": selected_source["unbind_date"],
        "bindStatus": status,
        # 新规则：仅在“非有绑定 且 未报名”时允许申请
        "canApply": (status != "有绑定") and int(is_enrolled) == 0,
    }


def issue_token(user_id: int, account: str, role: str) -> str:
    """签发登录令牌。"""
    payload = {"user_id": user_id, "account": account, "role": role}
    return serializer.dumps(payload)


def parse_token(token: str) -> Dict[str, Any]:
    """解析并校验登录令牌。"""
    return serializer.loads(token, max_age=TOKEN_EXPIRE_SECONDS)


def auth_required(required_role: Optional[str] = None):
    """
    鉴权装饰器。
    - 要求携带 Bearer Token
    - 可选要求指定角色
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"message": "未登录或登录已失效"}), 401

            token = auth_header.replace("Bearer ", "", 1).strip()
            if not token:
                return jsonify({"message": "未登录或登录已失效"}), 401

            try:
                user_payload = parse_token(token)
            except SignatureExpired:
                return jsonify({"message": "登录已过期，请重新登录"}), 401
            except BadSignature:
                return jsonify({"message": "登录凭证无效"}), 401

            if required_role and user_payload.get("role") != required_role:
                return jsonify({"message": "无权限访问"}), 403

            g.current_user = user_payload
            return func(*args, **kwargs)

        return wrapper

    return decorator


# ------------------------------
# 基础接口（阶段1）
# ------------------------------
@app.route("/", methods=["GET"])
def index() -> Any:
    """
    根路径说明接口。
    作用：提供简单中文欢迎页，并给出常用接口入口提示。
    """
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>意向学员绑定关系查询系统</title>
  <style>
    body { font-family: "Microsoft YaHei", Arial, sans-serif; background: #f7f8fa; color: #1f2329; margin: 0; }
    .card { max-width: 760px; margin: 60px auto; background: #fff; border: 1px solid #e5e6eb; border-radius: 10px; padding: 28px; box-shadow: 0 4px 16px rgba(0,0,0,0.04); }
    h1 { margin: 0 0 10px; font-size: 24px; }
    p { margin: 8px 0; line-height: 1.6; }
    .tips { margin-top: 16px; padding: 12px; background: #f2f3f5; border-radius: 8px; }
    code { background: #eef1f5; padding: 2px 6px; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>欢迎使用意向学员绑定关系查询系统</h1>
    <p>服务已启动，你可以通过后端接口进行联调与验收。</p>
    <div class="tips">
      <p>健康检查：<code>/api/health</code></p>
      <p>登录接口：<code>/api/auth/login</code></p>
      <p>说明：当前根路径为演示欢迎页，业务能力请通过 <code>/api/*</code> 访问。</p>
    </div>
  </div>
</body>
</html>
"""
    return Response(html, content_type="text/html; charset=utf-8")


@app.route("/frontend_demo.html", methods=["GET"])
def frontend_demo_page() -> Any:
    """
    前端联调演示页入口。
    作用：通过后端同源提供 HTML，避免 file:// 直开时的跨域限制。
    """
    return send_from_directory(BASE_DIR, "frontend_demo.html")


@app.route("/api/health", methods=["GET"])
def health() -> Any:
    """健康检查接口。"""
    return jsonify({"app": APP_NAME, "status": "ok", "time": current_time_str()})


@app.route("/api/auth/login", methods=["POST"])
def login() -> Any:
    """
    账号密码登录。
    规则：
    - 账号或密码错误时，统一提示，避免泄露账号存在性
    """
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account", "")).strip()
    password = str(payload.get("password", ""))

    if not account or not password:
        return jsonify({"message": "账号和密码不能为空"}), 400

    db = get_db()
    user = db.execute(
        "SELECT id, account, password_hash, role FROM users WHERE account = ?",
        (account,),
    ).fetchone()

    if user is None:
        return jsonify({"message": "账号或密码错误"}), 401

    if not check_password_hash(user["password_hash"], password):
        return jsonify({"message": "账号或密码错误"}), 401

    token = issue_token(user["id"], user["account"], user["role"])
    return jsonify(
        {
            "token": token,
            "role": user["role"],
            "account": user["account"],
            "expireSeconds": TOKEN_EXPIRE_SECONDS,
        }
    )


@app.route("/api/auth/me", methods=["GET"])
@auth_required()
def me() -> Any:
    """获取当前登录用户信息（用于前端鉴权态校验）。"""
    return jsonify({"user": g.current_user})


@app.route("/api/admin/bootstrap-status", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def bootstrap_status() -> Any:
    """
    返回基础初始化状态，便于联调验证阶段1是否完成。
    仅管理员可访问。
    """
    db = get_db()

    user_count = db.execute("SELECT COUNT(1) AS c FROM users").fetchone()["c"]
    prospect_count = db.execute("SELECT COUNT(1) AS c FROM prospects").fetchone()["c"]
    source_count = db.execute("SELECT COUNT(1) AS c FROM prospect_sources").fetchone()["c"]
    app_count = db.execute("SELECT COUNT(1) AS c FROM source_applications").fetchone()["c"]

    return jsonify(
        {
            "message": "阶段1基础能力已初始化",
            "statistics": {
                "users": user_count,
                "prospects": prospect_count,
                "prospectSources": source_count,
                "sourceApplications": app_count,
            },
        }
    )


@app.route("/api/rules/preview", methods=["POST"])
def preview_rule_result() -> Any:
    """
    绑定规则预览接口（用于前后端联调）。
    入参示例：
    {
      "bindDate": "20260320",
      "bindPeriodDays": 10,
      "sources": [{"source_wechat_id": "a", "bind_date": "20260320", "bind_period_days": 10}],
      "defaultBindPeriodDays": 7
    }
    """
    payload = request.get_json(silent=True) or {}
    bind_date = str(payload.get("bindDate", "")).strip()
    bind_period_days = payload.get("bindPeriodDays")
    sources = payload.get("sources") or []
    default_period = int(payload.get("defaultBindPeriodDays", 7))

    try:
        result: Dict[str, Any] = {}

        if bind_date and bind_period_days is not None:
            period = int(bind_period_days)
            result["unbindDate"] = BindingRuleService.calc_unbind_date(bind_date, period)
            result["bindStatus"] = BindingRuleService.calc_bind_status(bind_date, period)

        if isinstance(sources, list):
            selected = BindingRuleService.pick_display_source(sources, default_period)
            result["displaySource"] = selected

        return jsonify({"result": result})
    except (ValueError, TypeError):
        return jsonify({"message": "日期或绑定周期格式错误"}), 400


# ------------------------------
# 前台查询与申请（阶段2）
# ------------------------------
@app.route("/api/query/prospect", methods=["GET"])
@auth_required()
def query_prospect() -> Any:
    """
    前台查询接口：
    - 输入意向学员微信号
    - 返回当前展示来源（按优先级规则）及可申请标记
    """
    wechat_id = str(request.args.get("wechatId", "")).strip()
    if not wechat_id:
        return jsonify({"message": "意向学员微信号不能为空"}), 400

    db = get_db()
    prospect = db.execute(
        "SELECT id, wechat_id, is_enrolled FROM prospects WHERE wechat_id = ?",
        (wechat_id,),
    ).fetchone()
    if prospect is None:
        return jsonify({"message": "未找到该意向学员信息"}), 404

    rows = db.execute(
        """
        SELECT source_wechat_id, bind_date, bind_period_days
        FROM prospect_sources
        WHERE prospect_id = ?
        """,
        (prospect["id"],),
    ).fetchall()

    source_list = [dict(row) for row in rows]
    if not source_list:
        return jsonify(
            {
                "data": build_display_result(
                    prospect["wechat_id"], int(prospect["is_enrolled"]), None
                )
            }
        )

    default_period = get_default_bind_period_days(db)
    # 关键业务逻辑：若存在来源未设置绑定周期，则系统默认周期必须可用
    if any(item["bind_period_days"] is None for item in source_list) and default_period is None:
        return jsonify({"message": "系统默认绑定周期未配置，无法计算绑定状态"}), 400

    selected = BindingRuleService.pick_display_source(source_list, int(default_period or 0))
    return jsonify(
        {
            "data": build_display_result(
                prospect["wechat_id"], int(prospect["is_enrolled"]), selected
            )
        }
    )


@app.route("/api/query/source-application", methods=["POST"])
@auth_required()
def submit_source_application() -> Any:
    """
    提交“添加来源申请”。
    校验规则：
    1. 来源微信号必填
    2. 意向学员微信号截图必填（1张）
    3. 聊天记录截图必填（1~9张）
    4. 当前状态为“有绑定”时禁止提交
    """
    prospect_wechat_id = str(request.form.get("prospectWechatId", "")).strip()
    source_wechat_id = str(request.form.get("sourceWechatId", "")).strip()

    if not prospect_wechat_id:
        return jsonify({"message": "意向学员微信号不能为空"}), 400
    if not source_wechat_id:
        return jsonify({"message": "来源微信号不能为空"}), 400

    prospect_screenshot = request.files.get("prospectWechatScreenshot")
    chat_screenshots = request.files.getlist("chatScreenshots")

    if prospect_screenshot is None or not prospect_screenshot.filename:
        return jsonify({"message": "意向学员微信号截图为必填"}), 400

    if not chat_screenshots:
        return jsonify({"message": "聊天记录截图为必填"}), 400
    if len(chat_screenshots) > 9:
        return jsonify({"message": "聊天记录截图最多上传9张"}), 400

    # 校验聊天截图每一项都有文件名，避免空文件占位
    valid_chat_files = [f for f in chat_screenshots if f and f.filename]
    if not valid_chat_files:
        return jsonify({"message": "聊天记录截图为必填"}), 400
    if len(valid_chat_files) > 9:
        return jsonify({"message": "聊天记录截图最多上传9张"}), 400

    db = get_db()
    prospect = db.execute(
        "SELECT id, wechat_id, is_enrolled FROM prospects WHERE wechat_id = ?",
        (prospect_wechat_id,),
    ).fetchone()
    if int(prospect["is_enrolled"]) == 1:
        return jsonify({"message": "该意向学员已报名，不可提交添加来源申请"}), 400

    if prospect is None:
        return jsonify({"message": "未找到该意向学员信息"}), 404

    # 复用查询逻辑，计算当前展示状态以判断是否允许申请
    source_rows = db.execute(
        """
        SELECT source_wechat_id, bind_date, bind_period_days
        FROM prospect_sources
        WHERE prospect_id = ?
        """,
        (prospect["id"],),
    ).fetchall()
    source_list = [dict(row) for row in source_rows]

    if source_list:
        default_period = get_default_bind_period_days(db)
        if any(item["bind_period_days"] is None for item in source_list) and default_period is None:
            return jsonify({"message": "系统默认绑定周期未配置，无法判断是否可申请"}), 400
        selected = BindingRuleService.pick_display_source(source_list, int(default_period or 0))
        if selected and selected["bind_status"] == "有绑定":
            return jsonify({"message": "当前为有绑定状态，不可提交添加来源申请"}), 400

    now = datetime.now()
    today = current_date_yyyymmdd()
    default_period_days = get_default_bind_period_days(db)
    if default_period_days is None:
        return jsonify({"message": "系统默认绑定周期未配置，申请提交失败"}), 400

    # 为避免文件名冲突，按申请日期分目录 + 时间戳前缀保存文件
    day_dir = os.path.join(SOURCE_APPLICATION_UPLOAD_DIR, today)
    os.makedirs(day_dir, exist_ok=True)

    def save_upload_file(file_obj: Any, prefix: str) -> str:
        """保存上传文件并返回相对路径。"""
        raw_name = file_obj.filename or "unknown.png"
        safe_name = secure_filename(raw_name) or "image.png"
        timestamp = now.strftime("%H%M%S%f")
        stored_name = "{}_{}_{}".format(prefix, timestamp, safe_name)
        abs_path = os.path.join(day_dir, stored_name)
        file_obj.save(abs_path)
        # 统一保存相对路径，后续便于迁移存储
        rel_path = os.path.relpath(abs_path, BASE_DIR).replace("\\", "/")
        return rel_path

    try:
        prospect_shot_path = save_upload_file(prospect_screenshot, "prospect")
        chat_paths: List[str] = []
        for idx, chat_file in enumerate(valid_chat_files, start=1):
            chat_paths.append(save_upload_file(chat_file, "chat{}".format(idx)))

        db.execute(
            """
            INSERT INTO source_applications (
                prospect_id, source_wechat_id, apply_date, bind_period_days,
                prospect_wechat_screenshot_url, chat_screenshots_json, status,
                reviewed_by, reviewed_at, review_remark, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?, ?)
            """,
            (
                prospect["id"],
                source_wechat_id,
                today,
                default_period_days,
                prospect_shot_path,
                json.dumps(chat_paths, ensure_ascii=False),
                current_time_str(),
                current_time_str(),
            ),
        )
        db.commit()
    except Exception:
        # 数据写入异常时回滚，避免出现数据库半写入状态
        db.rollback()
        return jsonify({"message": "申请提交失败，请稍后重试"}), 500

    return jsonify(
        {
            "message": "申请提交成功，已进入待审核队列",
            "data": {
                "prospectWechatId": prospect_wechat_id,
                "sourceWechatId": source_wechat_id,
                "applyDate": today,
                "bindPeriodDays": default_period_days,
                "status": "pending",
            },
        }
    )


# ------------------------------
# 后台用户管理（阶段3-第一部分）
# ------------------------------
@app.route("/api/admin/users", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_list_users() -> Any:
    """
    用户列表查询接口。
    支持按账号与角色筛选。
    """
    account_kw = str(request.args.get("account", "")).strip()
    role_kw_raw = request.args.get("role")
    role_kw = None
    if role_kw_raw is not None and str(role_kw_raw).strip():
        role_kw = normalize_role(role_kw_raw)
        if role_kw is None:
            return jsonify({"message": "角色参数不合法，仅支持 admin/user/管理员/普通用户"}), 400

    sql = "SELECT id, account, password_plain, role, created_at, updated_at FROM users WHERE 1=1"
    params: List[Any] = []
    if account_kw:
        sql += " AND account LIKE ?"
        params.append("%{}%".format(account_kw))
    if role_kw:
        sql += " AND role = ?"
        params.append(role_kw)
    sql += " ORDER BY id ASC"

    db = get_db()
    rows = db.execute(sql, tuple(params)).fetchall()
    return jsonify({"data": [build_user_view(row) for row in rows]})


@app.route("/api/admin/users", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_create_user() -> Any:
    """
    新增用户接口（单条）。
    """
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account", "")).strip()
    password = str(payload.get("password", ""))
    role_value = normalize_role(payload.get("role"))

    if not account:
        return jsonify({"message": "账号不能为空"}), 400
    if not password:
        return jsonify({"message": "密码不能为空"}), 400
    if role_value is None:
        return jsonify({"message": "角色不合法，仅支持 admin/user/管理员/普通用户"}), 400

    db = get_db()
    existed = db.execute("SELECT id FROM users WHERE account = ?", (account,)).fetchone()
    if existed is not None:
        return jsonify({"message": "账号已存在"}), 400

    now = current_time_str()
    cursor = db.execute(
        """
        INSERT INTO users (account, password_hash, password_plain, role, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (account, generate_password_hash(password), password, role_value, now, now),
    )
    db.commit()

    created = db.execute(
        "SELECT id, account, password_plain, role, created_at, updated_at FROM users WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify({"message": "新增用户成功", "data": build_user_view(created)})


@app.route("/api/admin/users/<int:user_id>", methods=["PUT"])
@auth_required(required_role=ROLE_ADMIN)
def admin_update_user(user_id: int) -> Any:
    """
    编辑用户接口。
    支持修改账号、密码、角色。
    """
    payload = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute(
        "SELECT id, account, password_plain, role, created_at, updated_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return jsonify({"message": "用户不存在"}), 404

    has_account = "account" in payload
    has_password = "password" in payload
    has_role = "role" in payload
    if not (has_account or has_password or has_role):
        return jsonify({"message": "至少需要提供一个可编辑字段(account/password/role)"}), 400

    new_account = row["account"]
    new_password_plain = row["password_plain"]
    new_password_hash = None
    new_role = row["role"]

    if has_account:
        candidate = str(payload.get("account", "")).strip()
        if not candidate:
            return jsonify({"message": "账号不能为空"}), 400
        duplicate = db.execute(
            "SELECT id FROM users WHERE account = ? AND id <> ?",
            (candidate, user_id),
        ).fetchone()
        if duplicate is not None:
            return jsonify({"message": "账号已存在"}), 400
        new_account = candidate

    if has_password:
        candidate_password = str(payload.get("password", ""))
        if not candidate_password:
            return jsonify({"message": "密码不能为空"}), 400
        new_password_plain = candidate_password
        new_password_hash = generate_password_hash(candidate_password)

    if has_role:
        candidate_role = normalize_role(payload.get("role"))
        if candidate_role is None:
            return jsonify({"message": "角色不合法，仅支持 admin/user/管理员/普通用户"}), 400
        new_role = candidate_role

    now = current_time_str()
    if new_password_hash is None:
        db.execute(
            """
            UPDATE users
            SET account = ?, password_plain = ?, role = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_account, new_password_plain, new_role, now, user_id),
        )
    else:
        db.execute(
            """
            UPDATE users
            SET account = ?, password_hash = ?, password_plain = ?, role = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_account, new_password_hash, new_password_plain, new_role, now, user_id),
        )
    db.commit()

    updated = db.execute(
        "SELECT id, account, password_plain, role, created_at, updated_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return jsonify({"message": "编辑用户成功", "data": build_user_view(updated)})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@auth_required(required_role=ROLE_ADMIN)
def admin_delete_user(user_id: int) -> Any:
    """
    删除用户接口（单条）。
    为避免误删当前登录管理员，禁止删除自己。
    """
    current_user_id = int(g.current_user["user_id"])
    if user_id == current_user_id:
        return jsonify({"message": "禁止删除当前登录账号"}), 400

    db = get_db()
    row = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return jsonify({"message": "用户不存在"}), 404

    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"message": "删除用户成功"})


@app.route("/api/admin/users/batch-delete", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_batch_delete_users() -> Any:
    """
    批量删除用户接口。
    入参支持：
    - {"ids": [1,2,3]}
    - {"accounts": ["yh001","yh002"]}
    """
    payload = request.get_json(silent=True) or {}
    ids_raw = payload.get("ids")
    accounts_raw = payload.get("accounts")

    db = get_db()
    current_user_id = int(g.current_user["user_id"])

    deleted_count = 0
    skipped_current_user = False

    if isinstance(ids_raw, list) and ids_raw:
        ids: List[int] = []
        for item in ids_raw:
            try:
                value = int(item)
            except (TypeError, ValueError):
                return jsonify({"message": "ids 中存在非法值"}), 400
            if value > 0:
                ids.append(value)
        if not ids:
            return jsonify({"message": "ids 不能为空"}), 400

        unique_ids = sorted(set(ids))
        if current_user_id in unique_ids:
            skipped_current_user = True
            unique_ids = [x for x in unique_ids if x != current_user_id]

        if unique_ids:
            placeholders = ",".join(["?"] * len(unique_ids))
            cursor = db.execute(
                "DELETE FROM users WHERE id IN ({})".format(placeholders),
                tuple(unique_ids),
            )
            deleted_count = cursor.rowcount
            db.commit()
    elif isinstance(accounts_raw, list) and accounts_raw:
        accounts = [str(x).strip() for x in accounts_raw if str(x).strip()]
        if not accounts:
            return jsonify({"message": "accounts 不能为空"}), 400
        unique_accounts = sorted(set(accounts))

        # 查询当前用户账号，避免被批量删除
        current_row = db.execute("SELECT account FROM users WHERE id = ?", (current_user_id,)).fetchone()
        current_account = current_row["account"] if current_row else ""
        if current_account and current_account in unique_accounts:
            skipped_current_user = True
            unique_accounts = [x for x in unique_accounts if x != current_account]

        if unique_accounts:
            placeholders = ",".join(["?"] * len(unique_accounts))
            cursor = db.execute(
                "DELETE FROM users WHERE account IN ({})".format(placeholders),
                tuple(unique_accounts),
            )
            deleted_count = cursor.rowcount
            db.commit()
    else:
        return jsonify({"message": "请提供 ids 或 accounts 作为批量删除条件"}), 400

    return jsonify(
        {
            "message": "批量删除完成",
            "data": {
                "deletedCount": deleted_count,
                "skippedCurrentUser": skipped_current_user,
            },
        }
    )


@app.route("/api/admin/users/import-json", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_import_users_json() -> Any:
    """
    JSON 批量导入用户接口。
    兼容以下字段：
    - 中文键：账号 / 密码 / 角色
    - 英文键：account / password / role
    """
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"message": "请求体必须为 JSON"}), 400

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("users"), list):
        items = payload.get("users")
    else:
        return jsonify({"message": "JSON 格式错误，应为数组或包含 users 数组"}), 400

    if not items:
        return jsonify({"message": "导入数据不能为空"}), 400

    parsed_items: List[Dict[str, Any]] = []
    account_set: set = set()
    errors: List[str] = []

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append("第{}条不是对象结构".format(idx))
            continue

        account = str(item.get("账号", item.get("account", ""))).strip()
        password = str(item.get("密码", item.get("password", "")))
        role_raw = item.get("角色", item.get("role"))
        role_value = normalize_role(role_raw)

        if not account:
            errors.append("第{}条账号为空".format(idx))
        if not password:
            errors.append("第{}条密码为空".format(idx))
        if role_value is None:
            errors.append("第{}条角色不合法".format(idx))

        if account:
            if account in account_set:
                errors.append("第{}条账号与导入内其他记录重复".format(idx))
            account_set.add(account)

        parsed_items.append(
            {
                "account": account,
                "password": password,
                "role": role_value,
            }
        )

    if errors:
        return jsonify({"message": "导入数据校验失败", "errors": errors}), 400

    db = get_db()
    exists_rows = db.execute(
        "SELECT account FROM users WHERE account IN ({})".format(",".join(["?"] * len(account_set))),
        tuple(sorted(account_set)),
    ).fetchall()
    if exists_rows:
        existed_accounts = [row["account"] for row in exists_rows]
        return jsonify({"message": "导入失败，存在重复账号", "accounts": existed_accounts}), 400

    now = current_time_str()
    try:
        for item in parsed_items:
            db.execute(
                """
                INSERT INTO users (account, password_hash, password_plain, role, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["account"],
                    generate_password_hash(item["password"]),
                    item["password"],
                    item["role"],
                    now,
                    now,
                ),
            )
        db.commit()
    except Exception:
        db.rollback()
        return jsonify({"message": "导入失败，请检查数据后重试"}), 500

    return jsonify({"message": "导入成功", "data": {"importedCount": len(parsed_items)}})


@app.route("/api/admin/users/export-json", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_export_users_json() -> Any:
    """
    JSON 批量导出用户接口。
    导出字段按需求使用中文键：账号、密码、角色。
    """
    db = get_db()
    rows = db.execute(
        "SELECT account, password_plain, role FROM users ORDER BY id ASC"
    ).fetchall()

    exported = []
    for row in rows:
        exported.append(
            {
                "账号": row["account"],
                "密码": row["password_plain"],
                "角色": role_label(row["role"]),
            }
        )
    return jsonify(exported)


def validate_yyyymmdd(date_text: str) -> bool:
    """校验日期字符串是否为合法 yyyyMMdd。"""
    try:
        BindingRuleService.parse_yyyymmdd(date_text)
        return True
    except ValueError:
        return False


def enrich_sources_with_rule(
    source_rows: List[sqlite3.Row], default_period_days: Optional[int]
) -> Optional[List[Dict[str, Any]]]:
    """
    将来源记录补全为带计算字段的数据结构。
    当存在来源记录未设置绑定周期且系统默认周期未配置时，返回 None 作为错误信号。
    """
    sources = [dict(row) for row in source_rows]
    if not sources:
        return []

    if any(item["bind_period_days"] is None for item in sources):
        if default_period_days is None:
            return None

    enriched: List[Dict[str, Any]] = []
    for item in sources:
        period_days = item["bind_period_days"]
        if period_days is None:
            period_days = default_period_days
        period_days = int(period_days)

        enriched.append(
            {
                **item,
                "bind_period_days": period_days,
                "unbind_date": BindingRuleService.calc_unbind_date(item["bind_date"], period_days),
                "bind_status": BindingRuleService.calc_bind_status(item["bind_date"], period_days),
            }
        )
    return enriched


def count_active_sources(enriched_sources: List[Dict[str, Any]]) -> int:
    """统计来源中绑定状态为“有绑定”的数量。"""
    return sum(1 for item in enriched_sources if item.get("bind_status") == "有绑定")


def fetch_prospect_current_display(
    db: sqlite3.Connection, prospect_id: int
) -> Dict[str, Any]:
    """
    获取意向学员当前展示来源与状态。
    返回结构：
    - ok: True/False
    - error: 错误信息（仅 ok=False）
    - selected: 选中的来源（可能为 None）
    """
    rows = db.execute(
        """
        SELECT source_wechat_id, bind_date, bind_period_days
        FROM prospect_sources
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchall()

    default_period = get_default_bind_period_days(db)
    enriched = enrich_sources_with_rule(rows, default_period)
    if enriched is None:
        return {"ok": False, "error": "系统默认绑定周期未配置，无法计算绑定状态", "selected": None}

    selected = BindingRuleService.pick_display_source(enriched or [], int(default_period or 0))
    return {"ok": True, "selected": selected}


def build_prospect_list_item(
    prospect_row: sqlite3.Row,
    selected_source: Optional[Dict[str, Any]],
    source_count: int,
    application_count: int,
    pending_application_count: int,
    active_source_count: int,
) -> Dict[str, Any]:
    """构建后台意向学员列表行数据（8字段扩展版）。"""
    if not selected_source:
        return {
            "id": prospect_row["id"],
            "prospectWechatId": prospect_row["wechat_id"],
            "isEnrolled": int(prospect_row["is_enrolled"]),
            "isEnrolledLabel": enrolled_label(int(prospect_row["is_enrolled"])),
            "sourceWechatId": "",
            "bindDate": "",
            "bindPeriodDays": None,
            "unbindDate": "",
            "bindStatus": "",
            "sourceRecordsCount": source_count,
            "sourceApplicationsCount": application_count,
            "pendingSourceApplicationsCount": pending_application_count,
            "activeSourceCount": active_source_count,
        }

    return {
        "id": prospect_row["id"],
        "prospectWechatId": prospect_row["wechat_id"],
        "isEnrolled": int(prospect_row["is_enrolled"]),
        "isEnrolledLabel": enrolled_label(int(prospect_row["is_enrolled"])),
        "sourceWechatId": selected_source["source_wechat_id"],
        "bindDate": selected_source["bind_date"],
        "bindPeriodDays": selected_source["bind_period_days"],
        "unbindDate": selected_source["unbind_date"],
        "bindStatus": selected_source["bind_status"],
        "sourceRecordsCount": source_count,
        "sourceApplicationsCount": application_count,
        "pendingSourceApplicationsCount": pending_application_count,
        "activeSourceCount": active_source_count,
    }


# ------------------------------
# 后台意向学员管理（阶段3-第二部分）
# ------------------------------
@app.route("/api/admin/prospects", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_list_prospects() -> Any:
    """
    意向学员列表查询（支持分页与多条件筛选）。
    支持筛选：
    - wechatId：微信号模糊查询
    - isEnrolled：报名状态筛选（0/1/未报名/已报名）
    - pendingOnly：仅显示有未处理申请记录
    - multiActiveOnly：仅显示“有绑定来源数量 >= 2”的记录
    """
    keyword = str(request.args.get("wechatId", "")).strip()
    page = int(request.args.get("page", 1) or 1)
    page_size = int(request.args.get("pageSize", 10) or 10)
    is_enrolled_raw = request.args.get("isEnrolled")
    pending_only = parse_bool_query_arg(request.args.get("pendingOnly"))
    multi_active_only = parse_bool_query_arg(request.args.get("multiActiveOnly"))

    if page < 1:
        page = 1
    if page_size not in (10, 50, 100, 500):
        page_size = 10

    is_enrolled_filter = None
    if is_enrolled_raw is not None and str(is_enrolled_raw).strip():
        is_enrolled_filter = normalize_enrolled(is_enrolled_raw)
        if is_enrolled_filter is None:
            return jsonify({"message": "isEnrolled 参数不合法，仅支持 0/1/未报名/已报名"}), 400

    db = get_db()
    sql = "SELECT id, wechat_id, is_enrolled, created_at, updated_at FROM prospects WHERE 1=1"
    params: List[Any] = []
    if keyword:
        sql += " AND wechat_id LIKE ?"
        params.append("%{}%".format(keyword))
    if is_enrolled_filter is not None:
        sql += " AND is_enrolled = ?"
        params.append(int(is_enrolled_filter))
    sql += " ORDER BY id ASC"

    prospects = db.execute(sql, tuple(params)).fetchall()
    all_items = []
    for row in prospects:
        # 统一读取来源并计算展示值与“有绑定数量”
        source_rows = db.execute(
            """
            SELECT source_wechat_id, bind_date, bind_period_days
            FROM prospect_sources
            WHERE prospect_id = ?
            """,
            (row["id"],),
        ).fetchall()
        default_period = get_default_bind_period_days(db)
        enriched_sources = enrich_sources_with_rule(source_rows, default_period)
        if enriched_sources is None:
            return jsonify({"message": "系统默认绑定周期未配置，无法计算绑定状态"}), 400
        selected = BindingRuleService.pick_display_source(
            enriched_sources or [], int(default_period or 0)
        )
        active_source_count = count_active_sources(enriched_sources or [])

        source_count = db.execute(
            "SELECT COUNT(1) AS c FROM prospect_sources WHERE prospect_id = ?",
            (row["id"],),
        ).fetchone()["c"]
        app_count = db.execute(
            "SELECT COUNT(1) AS c FROM source_applications WHERE prospect_id = ?",
            (row["id"],),
        ).fetchone()["c"]
        pending_app_count = db.execute(
            """
            SELECT COUNT(1) AS c
            FROM source_applications
            WHERE prospect_id = ? AND status = 'pending'
            """,
            (row["id"],),
        ).fetchone()["c"]

        if pending_only and pending_app_count == 0:
            continue
        if multi_active_only and active_source_count < 2:
            continue

        all_items.append(
            build_prospect_list_item(
                row,
                selected,
                source_count,
                app_count,
                pending_app_count,
                active_source_count,
            )
        )

    total = len(all_items)
    start = (page - 1) * page_size
    end = start + page_size
    paged_items = all_items[start:end]

    return jsonify(
        {
            "data": paged_items,
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "total": total,
                "totalPages": (total + page_size - 1) // page_size if page_size else 0,
            },
        }
    )


@app.route("/api/admin/prospects", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_create_prospect() -> Any:
    """新增意向学员（单条）。"""
    payload = request.get_json(silent=True) or {}
    wechat_id = str(payload.get("wechatId", payload.get("意向学员微信号", ""))).strip()
    is_enrolled = normalize_enrolled(payload.get("isEnrolled", payload.get("是否报名", 0)))
    if not wechat_id:
        return jsonify({"message": "意向学员微信号不能为空"}), 400
    if is_enrolled is None:
        return jsonify({"message": "是否报名参数不合法，仅支持 0/1/未报名/已报名"}), 400

    db = get_db()
    existed = db.execute(
        "SELECT id FROM prospects WHERE wechat_id = ?",
        (wechat_id,),
    ).fetchone()
    if existed is not None:
        return jsonify({"message": "意向学员微信号已存在"}), 400

    now = current_time_str()
    cursor = db.execute(
        "INSERT INTO prospects (wechat_id, is_enrolled, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (wechat_id, int(is_enrolled), now, now),
    )
    db.commit()

    created = db.execute(
        "SELECT id, wechat_id, is_enrolled, created_at, updated_at FROM prospects WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify(
        {
            "message": "新增意向学员成功",
            "data": {
                "id": created["id"],
                "wechatId": created["wechat_id"],
                "isEnrolled": int(created["is_enrolled"]),
                "isEnrolledLabel": enrolled_label(int(created["is_enrolled"])),
                "createdAt": created["created_at"],
                "updatedAt": created["updated_at"],
            },
        }
    )


@app.route("/api/admin/prospects/<int:prospect_id>", methods=["PUT"])
@auth_required(required_role=ROLE_ADMIN)
def admin_update_prospect(prospect_id: int) -> Any:
    """编辑意向学员微信号与报名状态。"""
    payload = request.get_json(silent=True) or {}
    wechat_id = str(payload.get("wechatId", payload.get("意向学员微信号", ""))).strip() if (
        "wechatId" in payload or "意向学员微信号" in payload
    ) else None
    is_enrolled = normalize_enrolled(payload.get("isEnrolled", payload.get("是否报名"))) if (
        "isEnrolled" in payload or "是否报名" in payload
    ) else None

    if wechat_id is None and is_enrolled is None:
        return jsonify({"message": "至少需要提供 wechatId 或 isEnrolled 字段"}), 400
    if wechat_id is not None and not wechat_id:
        return jsonify({"message": "意向学员微信号不能为空"}), 400
    if ("isEnrolled" in payload or "是否报名" in payload) and is_enrolled is None:
        return jsonify({"message": "是否报名参数不合法，仅支持 0/1/未报名/已报名"}), 400

    db = get_db()
    existed = db.execute("SELECT id FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
    if existed is None:
        return jsonify({"message": "意向学员不存在"}), 404

    if wechat_id is not None:
        duplicate = db.execute(
            "SELECT id FROM prospects WHERE wechat_id = ? AND id <> ?",
            (wechat_id, prospect_id),
        ).fetchone()
        if duplicate is not None:
            return jsonify({"message": "意向学员微信号已存在"}), 400

    now = current_time_str()
    update_parts = []
    params: List[Any] = []
    if wechat_id is not None:
        update_parts.append("wechat_id = ?")
        params.append(wechat_id)
    if is_enrolled is not None:
        update_parts.append("is_enrolled = ?")
        params.append(int(is_enrolled))
    update_parts.append("updated_at = ?")
    params.append(now)
    params.append(prospect_id)
    db.execute(
        "UPDATE prospects SET {} WHERE id = ?".format(", ".join(update_parts)),
        tuple(params),
    )
    db.commit()
    return jsonify({"message": "编辑意向学员成功"})


@app.route("/api/admin/prospects/<int:prospect_id>", methods=["DELETE"])
@auth_required(required_role=ROLE_ADMIN)
def admin_delete_prospect(prospect_id: int) -> Any:
    """删除意向学员（单条），并清理关联来源与申请记录。"""
    db = get_db()
    existed = db.execute("SELECT id FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
    if existed is None:
        return jsonify({"message": "意向学员不存在"}), 404

    try:
        db.execute("DELETE FROM prospect_sources WHERE prospect_id = ?", (prospect_id,))
        db.execute("DELETE FROM source_applications WHERE prospect_id = ?", (prospect_id,))
        db.execute("DELETE FROM prospects WHERE id = ?", (prospect_id,))
        db.commit()
    except Exception:
        db.rollback()
        return jsonify({"message": "删除失败，请稍后重试"}), 500

    return jsonify({"message": "删除意向学员成功"})


@app.route("/api/admin/prospects/batch-delete", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_batch_delete_prospects() -> Any:
    """批量删除意向学员（支持 ids 或 wechatIds）。"""
    payload = request.get_json(silent=True) or {}
    ids_raw = payload.get("ids")
    wechat_ids_raw = payload.get("wechatIds")

    db = get_db()
    target_ids: List[int] = []
    if isinstance(ids_raw, list) and ids_raw:
        for item in ids_raw:
            try:
                value = int(item)
            except (TypeError, ValueError):
                return jsonify({"message": "ids 中存在非法值"}), 400
            if value > 0:
                target_ids.append(value)
    elif isinstance(wechat_ids_raw, list) and wechat_ids_raw:
        clean_wechat_ids = [str(x).strip() for x in wechat_ids_raw if str(x).strip()]
        if not clean_wechat_ids:
            return jsonify({"message": "wechatIds 不能为空"}), 400
        placeholders = ",".join(["?"] * len(clean_wechat_ids))
        rows = db.execute(
            "SELECT id FROM prospects WHERE wechat_id IN ({})".format(placeholders),
            tuple(sorted(set(clean_wechat_ids))),
        ).fetchall()
        target_ids = [row["id"] for row in rows]
    else:
        return jsonify({"message": "请提供 ids 或 wechatIds 作为删除条件"}), 400

    if not target_ids:
        return jsonify({"message": "未匹配到可删除的意向学员"}), 400

    unique_ids = sorted(set(target_ids))
    placeholders = ",".join(["?"] * len(unique_ids))

    try:
        db.execute(
            "DELETE FROM prospect_sources WHERE prospect_id IN ({})".format(placeholders),
            tuple(unique_ids),
        )
        db.execute(
            "DELETE FROM source_applications WHERE prospect_id IN ({})".format(placeholders),
            tuple(unique_ids),
        )
        cursor = db.execute(
            "DELETE FROM prospects WHERE id IN ({})".format(placeholders),
            tuple(unique_ids),
        )
        db.commit()
    except Exception:
        db.rollback()
        return jsonify({"message": "批量删除失败，请稍后重试"}), 500

    return jsonify(
        {
            "message": "批量删除成功",
            "data": {"deletedCount": cursor.rowcount},
        }
    )


@app.route("/api/admin/prospects/<int:prospect_id>/sources", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_list_prospect_sources(prospect_id: int) -> Any:
    """查看意向学员的全部来源记录。"""
    db = get_db()
    prospect = db.execute(
        "SELECT id, wechat_id, is_enrolled FROM prospects WHERE id = ?",
        (prospect_id,),
    ).fetchone()
    if prospect is None:
        return jsonify({"message": "意向学员不存在"}), 404

    rows = db.execute(
        """
        SELECT id, source_wechat_id, bind_date, bind_period_days, created_by, created_at
        FROM prospect_sources
        WHERE prospect_id = ?
        ORDER BY bind_date DESC, id DESC
        """,
        (prospect_id,),
    ).fetchall()
    default_period = get_default_bind_period_days(db)
    enriched = enrich_sources_with_rule(rows, default_period)
    if enriched is None:
        return jsonify({"message": "系统默认绑定周期未配置，无法计算来源记录"}), 400

    result = []
    for item in enriched:
        result.append(
            {
                "id": item["id"],
                "prospectWechatId": prospect["wechat_id"],
                "isEnrolled": int(prospect["is_enrolled"]),
                "isEnrolledLabel": enrolled_label(int(prospect["is_enrolled"])),
                "sourceWechatId": item["source_wechat_id"],
                "bindDate": item["bind_date"],
                "bindPeriodDays": item["bind_period_days"],
                "unbindDate": item["unbind_date"],
                "bindStatus": item["bind_status"],
                "createdBy": item.get("created_by"),
                "createdAt": item.get("created_at"),
            }
        )
    return jsonify({"data": result})


@app.route("/api/admin/prospects/<int:prospect_id>/sources", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_add_prospect_source(prospect_id: int) -> Any:
    """
    手动新增来源记录。
    限制：当当前绑定状态为“有绑定”时禁止添加。
    """
    payload = request.get_json(silent=True) or {}
    source_wechat_id = str(payload.get("sourceWechatId", payload.get("来源微信号", ""))).strip()
    bind_date = str(payload.get("bindDate", payload.get("绑定日期", ""))).strip()
    bind_period_days = payload.get("bindPeriodDays", payload.get("绑定周期"))

    if not source_wechat_id:
        return jsonify({"message": "来源微信号不能为空"}), 400
    if not bind_date:
        return jsonify({"message": "绑定日期不能为空"}), 400
    if not validate_yyyymmdd(bind_date):
        return jsonify({"message": "绑定日期格式错误，需为yyyyMMdd"}), 400
    if bind_period_days is None:
        return jsonify({"message": "绑定周期不能为空"}), 400
    try:
        bind_period_days_int = int(bind_period_days)
    except (TypeError, ValueError):
        return jsonify({"message": "绑定周期必须为正整数"}), 400
    if bind_period_days_int <= 0:
        return jsonify({"message": "绑定周期必须为正整数"}), 400

    db = get_db()
    prospect = db.execute(
        "SELECT id, wechat_id, is_enrolled FROM prospects WHERE id = ?",
        (prospect_id,),
    ).fetchone()
    if prospect is None:
        return jsonify({"message": "意向学员不存在"}), 404

    display_result = fetch_prospect_current_display(db, prospect_id)
    if not display_result["ok"]:
        return jsonify({"message": display_result["error"]}), 400
    selected = display_result["selected"]
    if selected is not None and selected["bind_status"] == "有绑定":
        return jsonify({"message": "当前绑定状态为有绑定，禁止新增来源记录"}), 400
    if int(prospect["is_enrolled"]) == 1:
        return jsonify({"message": "当前报名状态为已报名，禁止新增来源记录"}), 400

    now = current_time_str()
    db.execute(
        """
        INSERT INTO prospect_sources (
            prospect_id, source_wechat_id, bind_date, bind_period_days,
            created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prospect_id,
            source_wechat_id,
            bind_date,
            bind_period_days_int,
            g.current_user["account"],
            now,
            now,
        ),
    )
    db.commit()
    return jsonify({"message": "新增来源记录成功"})


@app.route("/api/admin/prospects/<int:prospect_id>/applications", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_list_prospect_applications(prospect_id: int) -> Any:
    """查看意向学员的全部来源申请记录。"""
    pending_only = parse_bool_query_arg(request.args.get("pendingOnly"))
    db = get_db()
    prospect = db.execute(
        "SELECT id, wechat_id, is_enrolled FROM prospects WHERE id = ?",
        (prospect_id,),
    ).fetchone()
    if prospect is None:
        return jsonify({"message": "意向学员不存在"}), 404

    rows = db.execute(
        """
        SELECT id, source_wechat_id, apply_date, bind_period_days, status, reviewed_by, reviewed_at, created_at
        FROM source_applications
        WHERE prospect_id = ?
        ORDER BY id DESC
        """,
        (prospect_id,),
    ).fetchall()

    default_period = get_default_bind_period_days(db)
    result = []
    for row in rows:
        if pending_only and row["status"] != "pending":
            continue
        period_days = row["bind_period_days"]
        if period_days is None:
            period_days = default_period
        if period_days is None:
            return jsonify({"message": "系统默认绑定周期未配置，无法计算申请记录"}), 400

        period_days_int = int(period_days)
        result.append(
            {
                "id": row["id"],
                "prospectWechatId": prospect["wechat_id"],
                "isEnrolled": int(prospect["is_enrolled"]),
                "isEnrolledLabel": enrolled_label(int(prospect["is_enrolled"])),
                "sourceWechatId": row["source_wechat_id"],
                "bindDate": row["apply_date"],  # 申请日期作为绑定日期展示
                "bindPeriodDays": period_days_int,
                "unbindDate": BindingRuleService.calc_unbind_date(row["apply_date"], period_days_int),
                "bindStatus": BindingRuleService.calc_bind_status(row["apply_date"], period_days_int),
                "status": row["status"],
                "reviewedBy": row["reviewed_by"],
                "reviewedAt": row["reviewed_at"],
                "createdAt": row["created_at"],
            }
        )

    return jsonify({"data": result})


@app.route("/api/admin/applications/<int:application_id>", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_get_application_detail(application_id: int) -> Any:
    """查看申请详情（含截图信息）。"""
    db = get_db()
    row = db.execute(
        """
        SELECT sa.id, sa.prospect_id, sa.source_wechat_id, sa.apply_date, sa.bind_period_days,
               sa.prospect_wechat_screenshot_url, sa.chat_screenshots_json,
               sa.status, sa.reviewed_by, sa.reviewed_at, sa.review_remark, sa.created_at,
               p.wechat_id AS prospect_wechat_id
        FROM source_applications sa
        JOIN prospects p ON p.id = sa.prospect_id
        WHERE sa.id = ?
        """,
        (application_id,),
    ).fetchone()
    if row is None:
        return jsonify({"message": "申请记录不存在"}), 404

    default_period = get_default_bind_period_days(db)
    period_days = row["bind_period_days"] if row["bind_period_days"] is not None else default_period
    if period_days is None:
        return jsonify({"message": "系统默认绑定周期未配置，无法计算申请详情"}), 400
    period_days_int = int(period_days)

    try:
        chat_images = json.loads(row["chat_screenshots_json"])
        if not isinstance(chat_images, list):
            chat_images = []
    except Exception:
        chat_images = []

    return jsonify(
        {
            "data": {
                "id": row["id"],
                "prospectId": row["prospect_id"],
                "prospectWechatId": row["prospect_wechat_id"],
                "sourceWechatId": row["source_wechat_id"],
                "bindDate": row["apply_date"],
                "bindPeriodDays": period_days_int,
                "unbindDate": BindingRuleService.calc_unbind_date(row["apply_date"], period_days_int),
                "bindStatus": BindingRuleService.calc_bind_status(row["apply_date"], period_days_int),
                "status": row["status"],
                "prospectWechatScreenshot": row["prospect_wechat_screenshot_url"],
                "chatScreenshots": chat_images,
                "reviewedBy": row["reviewed_by"],
                "reviewedAt": row["reviewed_at"],
                "reviewRemark": row["review_remark"],
                "createdAt": row["created_at"],
            }
        }
    )


@app.route("/api/admin/applications/<int:application_id>/approve", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_approve_application(application_id: int) -> Any:
    """
    审核通过申请：
    - 将申请来源写入来源记录
    - 将申请状态置为 approved
    """
    db = get_db()
    row = db.execute(
        """
        SELECT id, prospect_id, source_wechat_id, apply_date, bind_period_days, status
        FROM source_applications
        WHERE id = ?
        """,
        (application_id,),
    ).fetchone()
    if row is None:
        return jsonify({"message": "申请记录不存在"}), 404
    if row["status"] != "pending":
        return jsonify({"message": "申请已处理，不可重复审核"}), 400

    period_days = row["bind_period_days"]
    if period_days is None:
        period_days = get_default_bind_period_days(db)
    if period_days is None:
        return jsonify({"message": "系统默认绑定周期未配置，无法通过申请"}), 400
    period_days_int = int(period_days)

    now = current_time_str()
    reviewer = g.current_user["account"]
    try:
        db.execute(
            """
            INSERT INTO prospect_sources (
                prospect_id, source_wechat_id, bind_date, bind_period_days,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["prospect_id"],
                row["source_wechat_id"],
                row["apply_date"],
                period_days_int,
                reviewer,
                now,
                now,
            ),
        )
        db.execute(
            """
            UPDATE source_applications
            SET status = 'approved', reviewed_by = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reviewer, now, now, application_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        return jsonify({"message": "审核通过失败，请稍后重试"}), 500

    return jsonify({"message": "审核通过成功"})


@app.route("/api/admin/applications/<int:application_id>/reject", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_reject_application(application_id: int) -> Any:
    """审核拒绝申请：仅更新申请状态，不写入来源记录。"""
    payload = request.get_json(silent=True) or {}
    remark = str(payload.get("remark", "")).strip()

    db = get_db()
    row = db.execute(
        "SELECT id, status FROM source_applications WHERE id = ?",
        (application_id,),
    ).fetchone()
    if row is None:
        return jsonify({"message": "申请记录不存在"}), 404
    if row["status"] != "pending":
        return jsonify({"message": "申请已处理，不可重复审核"}), 400

    now = current_time_str()
    db.execute(
        """
        UPDATE source_applications
        SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_remark = ?, updated_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (g.current_user["account"], now, remark, now, application_id),
    )
    db.commit()
    return jsonify({"message": "审核拒绝成功"})


@app.route("/api/admin/prospects/import-json", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_import_prospects_json() -> Any:
    """
    JSON 批量导入意向学员。
    支持字段：
    - 顶层：意向学员微信号
    - 来源：来源微信号、绑定日期、绑定周期(可选)
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, list) or not payload:
        return jsonify({"message": "JSON 导入格式错误，应为非空数组"}), 400

    db = get_db()
    default_period = get_default_bind_period_days(db)

    parsed: List[Dict[str, Any]] = []
    input_wechat_set = set()
    errors: List[str] = []

    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            errors.append("第{}条不是对象结构".format(idx))
            continue

        wechat_id = str(item.get("意向学员微信号", item.get("wechatId", ""))).strip()
        is_enrolled = normalize_enrolled(item.get("是否报名", item.get("isEnrolled", 0)))
        if not wechat_id:
            errors.append("第{}条意向学员微信号为空".format(idx))
            continue
        if is_enrolled is None:
            errors.append("第{}条是否报名不合法".format(idx))
            continue
        if wechat_id in input_wechat_set:
            errors.append("第{}条意向学员微信号重复".format(idx))
            continue
        input_wechat_set.add(wechat_id)

        sources_raw = item.get("来源", item.get("sources", []))
        if sources_raw is None:
            sources_raw = []
        if not isinstance(sources_raw, list):
            errors.append("第{}条来源字段必须为数组".format(idx))
            continue

        source_items: List[Dict[str, Any]] = []
        for s_idx, source in enumerate(sources_raw, start=1):
            if not isinstance(source, dict):
                errors.append("第{}条第{}个来源不是对象结构".format(idx, s_idx))
                continue
            source_wechat_id = str(
                source.get("来源微信号", source.get("sourceWechatId", ""))
            ).strip()
            bind_date = str(source.get("绑定日期", source.get("bindDate", ""))).strip()
            period_raw = source.get("绑定周期", source.get("bindPeriodDays"))

            if not source_wechat_id:
                errors.append("第{}条第{}个来源微信号为空".format(idx, s_idx))
            if not bind_date or not validate_yyyymmdd(bind_date):
                errors.append("第{}条第{}个绑定日期格式错误".format(idx, s_idx))

            period_value = None
            if period_raw is not None and str(period_raw).strip() != "":
                try:
                    period_value = int(period_raw)
                except (TypeError, ValueError):
                    errors.append("第{}条第{}个绑定周期必须为正整数".format(idx, s_idx))
                else:
                    if period_value <= 0:
                        errors.append("第{}条第{}个绑定周期必须为正整数".format(idx, s_idx))
            else:
                # 关键业务规则：未单独设置绑定周期时必须有系统默认值
                if default_period is None:
                    errors.append("第{}条第{}个来源缺少绑定周期且系统默认绑定周期未配置".format(idx, s_idx))

            source_items.append(
                {
                    "source_wechat_id": source_wechat_id,
                    "bind_date": bind_date,
                    "bind_period_days": period_value,
                }
            )

        parsed.append({"wechat_id": wechat_id, "is_enrolled": int(is_enrolled), "sources": source_items})

    if errors:
        return jsonify({"message": "导入数据校验失败", "errors": errors}), 400

    existed = db.execute(
        "SELECT wechat_id FROM prospects WHERE wechat_id IN ({})".format(",".join(["?"] * len(input_wechat_set))),
        tuple(sorted(input_wechat_set)),
    ).fetchall()
    if existed:
        duplicate_wechat = [row["wechat_id"] for row in existed]
        return jsonify({"message": "导入失败，意向学员微信号已存在", "wechatIds": duplicate_wechat}), 400

    now = current_time_str()
    try:
        for item in parsed:
            cursor = db.execute(
                "INSERT INTO prospects (wechat_id, is_enrolled, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (item["wechat_id"], item["is_enrolled"], now, now),
            )
            prospect_id = cursor.lastrowid
            for source in item["sources"]:
                db.execute(
                    """
                    INSERT INTO prospect_sources (
                        prospect_id, source_wechat_id, bind_date, bind_period_days,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prospect_id,
                        source["source_wechat_id"],
                        source["bind_date"],
                        source["bind_period_days"],
                        g.current_user["account"],
                        now,
                        now,
                    ),
                )
        db.commit()
    except Exception:
        db.rollback()
        return jsonify({"message": "导入失败，请稍后重试"}), 500

    return jsonify({"message": "导入成功", "data": {"importedCount": len(parsed)}})


@app.route("/api/admin/prospects/import-json-async", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_import_prospects_json_async() -> Any:
    """
    异步导入意向学员。
    说明：刷新页面后任务不会中断，可通过 jobId 查询进度。
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, list) or not payload:
        return jsonify({"message": "JSON 导入格式错误，应为非空数组"}), 400

    now = current_time_str()
    job_id = uuid.uuid4().hex
    db = get_db()
    db.execute(
        """
        INSERT INTO import_jobs (
            job_id, job_type, status, progress_percent, total_count,
            processed_count, success_count, failed_count, error_message,
            payload_json, created_by, created_at, updated_at, finished_at
        ) VALUES (?, 'prospects_import', 'queued', 0, 0, 0, 0, 0, NULL, ?, ?, ?, ?, NULL)
        """,
        (
            job_id,
            json.dumps(payload, ensure_ascii=False),
            g.current_user["account"],
            now,
            now,
        ),
    )
    db.commit()

    # 后台线程处理导入任务，避免请求阻塞
    worker = threading.Thread(target=process_import_job, args=(job_id,))
    worker.daemon = True
    worker.start()

    return jsonify(
        {
            "message": "导入任务已创建",
            "data": {"jobId": job_id, "status": "queued"},
        }
    )


@app.route("/api/admin/prospects/import-jobs/<job_id>", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_get_import_job_status(job_id: str) -> Any:
    """查询异步导入任务进度。"""
    db = get_db()
    row = db.execute(
        """
        SELECT job_id, job_type, status, progress_percent, total_count, processed_count,
               success_count, failed_count, error_message, created_by, created_at,
               updated_at, finished_at
        FROM import_jobs
        WHERE job_id = ? AND job_type = 'prospects_import'
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        return jsonify({"message": "导入任务不存在"}), 404

    return jsonify(
        {
            "data": {
                "jobId": row["job_id"],
                "jobType": row["job_type"],
                "status": row["status"],
                "progressPercent": row["progress_percent"],
                "totalCount": row["total_count"],
                "processedCount": row["processed_count"],
                "successCount": row["success_count"],
                "failedCount": row["failed_count"],
                "errorMessage": row["error_message"],
                "createdBy": row["created_by"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "finishedAt": row["finished_at"],
            }
        }
    )


@app.route("/api/admin/prospects/clear-all", methods=["POST"])
@auth_required(required_role=ROLE_ADMIN)
def admin_clear_all_prospects() -> Any:
    """一键删除所有意向学员及其来源、申请记录。"""
    db = get_db()
    try:
        db.execute("DELETE FROM source_applications")
        db.execute("DELETE FROM prospect_sources")
        db.execute("DELETE FROM prospects")
        db.commit()
    except Exception:
        db.rollback()
        return jsonify({"message": "一键删除失败，请稍后重试"}), 500
    return jsonify({"message": "已删除全部意向学员数据"})


@app.route("/api/admin/prospects/export-json", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_export_prospects_json() -> Any:
    """JSON 批量导出意向学员（含来源计算字段）。"""
    db = get_db()
    default_period = get_default_bind_period_days(db)

    prospects = db.execute(
        "SELECT id, wechat_id, is_enrolled FROM prospects ORDER BY id ASC"
    ).fetchall()

    exported = []
    for p in prospects:
        source_rows = db.execute(
            """
            SELECT source_wechat_id, bind_date, bind_period_days
            FROM prospect_sources
            WHERE prospect_id = ?
            ORDER BY bind_date ASC, id ASC
            """,
            (p["id"],),
        ).fetchall()
        enriched = enrich_sources_with_rule(source_rows, default_period)
        if enriched is None:
            return jsonify({"message": "系统默认绑定周期未配置，无法完成导出"}), 400

        exported_sources = []
        for source in enriched:
            exported_sources.append(
                {
                    "来源微信号": source["source_wechat_id"],
                    "绑定日期": source["bind_date"],
                    "绑定周期": source["bind_period_days"],
                    "解绑日期": source["unbind_date"],
                    "绑定状态": source["bind_status"],
                }
            )
        exported.append(
            {
                "意向学员微信号": p["wechat_id"],
                "是否报名": enrolled_label(int(p["is_enrolled"])),
                "来源": exported_sources,
            }
        )

    return jsonify(exported)


@app.route("/api/admin/bind-period/default", methods=["GET"])
@auth_required(required_role=ROLE_ADMIN)
def admin_get_default_bind_period() -> Any:
    """获取系统默认绑定周期（单位：天）。"""
    db = get_db()
    value = get_default_bind_period_days(db)
    return jsonify(
        {
            "data": {
                "defaultBindPeriodDays": value,
                "configured": value is not None,
            }
        }
    )


@app.route("/api/admin/bind-period/default", methods=["PUT"])
@auth_required(required_role=ROLE_ADMIN)
def admin_update_default_bind_period() -> Any:
    """设置系统默认绑定周期（单位：天，必须为正整数）。"""
    payload = request.get_json(silent=True) or {}
    value_raw = payload.get("defaultBindPeriodDays")

    if value_raw is None:
        return jsonify({"message": "defaultBindPeriodDays 不能为空"}), 400

    try:
        value = int(value_raw)
    except (TypeError, ValueError):
        return jsonify({"message": "defaultBindPeriodDays 必须为正整数"}), 400

    if value <= 0:
        return jsonify({"message": "defaultBindPeriodDays 必须为正整数"}), 400

    db = get_db()
    now = current_time_str()
    # 关键业务逻辑：先更新后插入，兼容旧版 SQLite（不依赖 UPSERT 语法）
    cursor = db.execute(
        """
        UPDATE system_configs
        SET config_value = ?, updated_at = ?
        WHERE config_key = 'default_bind_period_days'
        """,
        (str(value), now),
    )
    if cursor.rowcount == 0:
        db.execute(
            """
            INSERT INTO system_configs (config_key, config_value, updated_at)
            VALUES ('default_bind_period_days', ?, ?)
            """,
            (str(value), now),
        )
    db.commit()

    return jsonify(
        {
            "message": "默认绑定周期设置成功",
            "data": {"defaultBindPeriodDays": value},
        }
    )


def create_app() -> Flask:
    """应用工厂：确保导入时即完成数据库初始化。"""
    init_database()
    return app


if __name__ == "__main__":
    init_database()
    # 开发环境默认端口 8000，便于与其他项目区分
    app.run(host="127.0.0.1", port=8000, debug=True)
