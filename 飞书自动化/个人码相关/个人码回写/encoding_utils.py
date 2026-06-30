# -*- coding: utf-8 -*-
"""
编码工具核心层（防乱码 / 防 latin-1 报错 / 防控制台崩溃）
==============================================
本模块是"任何编码环境都不崩溃"的统一兜底层。

提供能力：
    1. setup_console()
       —— 把 Windows 控制台 stdout / stderr 强制改成 UTF-8，
          且 errors=replace，无法显示的字符也不会抛异常。

    2. safe_decode(file_path)
       —— 读取任意文本文件：先 utf-8-sig，再 utf-8，再 gbk，
          最后 errors=ignore 兜底，永远不抛 UnicodeDecodeError。

    3. clean_filename(name)
       —— 文件名清洗：去掉 Windows 非法字符，把 ¿ / 替换字符 / 控制字符
          统一替换为下划线，输出可安全用于匹配 / 显示 / 路径拼接的字符串。

    4. ascii_safe_name(name, default="upload.bin")
       —— 把任意字符串转成纯 ASCII 文件名，避免 requests 在
          multipart Content-Disposition 头里按 latin-1 编码时崩溃。
          这是修复 "UnicodeEncodeError: 'latin-1' codec can't encode" 的关键。

    5. safe_str(obj)
       —— 任何对象 → 打印安全的 str（替换无法编码的字符）。
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from typing import Any


# ────────────────────────── 1. 控制台编码修复 ──────────────────────────
def setup_console() -> None:
    """
    把 Python 进程的标准输出 / 标准错误强制设为 UTF-8。

    适用场景：
        - Windows 默认控制台是 GBK，遇到中文 / emoji / 表情会抛
          UnicodeEncodeError，导致脚本中途崩溃。
        - 即便 reconfigure 不可用（如旧 Python / 被 IDE 包装），
          也用 errors=replace 兜底，不让 print 崩溃。

    重点：同时设置 errors='replace'，比单纯 encoding='utf-8' 更稳。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            # Python 3.7+ 提供的运行时重配置
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # 兜底：尝试通过 detach + TextIOWrapper 包一层
            try:
                import io
                buf = stream.buffer if hasattr(stream, "buffer") else None
                if buf is not None:
                    setattr(
                        sys,
                        stream_name,
                        io.TextIOWrapper(
                            buf, encoding="utf-8", errors="replace", line_buffering=True
                        ),
                    )
            except Exception:
                # 最终兜底也失败时，宁可继续运行（上层 print 会被 safe_str 保护）
                pass

    # Windows: 顺便把 chcp 拉到 65001，避免子进程继承 GBK
    if os.name == "nt":
        try:
            os.system("chcp 65001 > nul")
        except Exception:
            pass


# ────────────────────────── 2. 文件读取（自动识别 GBK / UTF-8 / 带 BOM） ──────
def safe_decode(file_path: str) -> str:
    """
    安全读取文本文件，永不抛 UnicodeDecodeError。

    优先级：utf-8-sig → utf-8 → gbk → utf-8(errors=ignore)
    """
    with open(file_path, "rb") as f:
        raw = f.read()

    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 最终兜底：丢掉无法识别的字节
    return raw.decode("utf-8", errors="ignore")


# ────────────────────────── 3. 文件名清洗 ──────────────────────────
# Windows 路径非法字符： \ / : * ? " < > |
_ILLEGAL_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')

# 替换字符 U+FFFD（解码失败留下的 ）、问号倒置 ¿ 、控制字符
_BROKEN_CHARS = re.compile(
    r"[\u0000-\u001f\u007f"     # ASCII 控制字符
    r"\ufffd"                    # 替换字符
    r"\u00bf"                    # 倒置问号 ¿
    r"]"
)


def clean_filename(name: str) -> str:
    """
    清洗文件名，让它可以安全用于：
        - 字符串匹配（编号 in stem）
        - 控制台打印
        - 日志写入

    步骤：
        1. 容错为 str（None → ""）
        2. 去掉 ASCII 控制字符 / 替换字符 / 倒置问号 → "_"
        3. 去掉 Windows 路径非法字符 → ""
        4. NFC 归一化（避免组合字符 vs 预组合字符的匹配漂移）
        5. strip 首尾空白 / 下划线 / 点
        6. 全空时回填 "_unnamed_"
    """
    if name is None:
        return "_unnamed_"

    s = str(name)

    # 步骤 1：把破损 / 控制字符替换为下划线（保留长度感知，方便人工核对）
    s = _BROKEN_CHARS.sub("_", s)

    # 步骤 2：去掉 Windows 非法路径字符
    s = _ILLEGAL_PATH_CHARS.sub("", s)

    # 步骤 3：Unicode 归一化（NFC = 预组合形式，匹配最稳）
    try:
        s = unicodedata.normalize("NFC", s)
    except Exception:
        pass

    # 步骤 4：合并多余下划线 + 去首尾噪声
    s = re.sub(r"_+", "_", s).strip(" ._\t\r\n")

    return s or "_unnamed_"


# ────────────────────────── 4. ASCII 安全名（修 latin-1 报错） ──────────────
def ascii_safe_name(name: str, default: str = "upload.bin") -> str:
    """
    生成一个**纯 ASCII** 的文件名，专供 requests multipart 上传使用。

    背景：
        urllib3 在拼 multipart Content-Disposition 时会用 latin-1 编码
        filename。如果原名含中文 / ¿ / 替换字符，就会抛：
            UnicodeEncodeError: 'latin-1' codec can't encode characters
        飞书后端只关心二进制内容 + 显式传的 file_name 字段，所以
        multipart 这层的 filename 用一个 ASCII 兜底名即可。

    策略：
        1. 先做 NFKD + ascii(ignore)，能保留拼音 / 字母 / 数字
        2. 去掉非 [A-Za-z0-9._-] 的字符
        3. 全空 → default
    """
    if not name:
        return default
    try:
        nfkd = unicodedata.normalize("NFKD", str(name))
        ascii_only = nfkd.encode("ascii", errors="ignore").decode("ascii")
    except Exception:
        ascii_only = ""
    ascii_only = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("_.")
    return ascii_only or default


# ────────────────────────── 5. 任意对象 → 打印安全字符串 ──────────────────
def safe_str(obj: Any) -> str:
    """
    任意对象转 str，并保证不会因为编码问题导致下游 print / logger 崩溃。
    """
    try:
        s = str(obj)
    except Exception as e:
        return f"<unprintable: {type(obj).__name__}: {e}>"
    # 强制走一遍 utf-8(errors=replace)，把潜在的代理字符规范化
    try:
        return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        return repr(s)
