# -*- coding: utf-8 -*-
"""
批量生成个人码（纯 Python：psd_tools 读模板坐标 + Pillow 重新合成）
==============================================================

流程：
    1. 用 psd_tools 打开 PSD 模板，找到 三个图层：
         二维码 / 名字 / 手机号
       记录各自的 bbox（位置 + 宽高），并尝试读出文字图层的字号。
    2. 用 psd_tools.composite(layer_filter=...) 把模板渲染成底图，
       但**排除上述 3 个图层**，避免占位内容残留在底图上。
    3. 读取 读取后的内容.txt（逗号分隔），每行：
           编号-姓名,二维码本地路径,拼音,手机号
    4. 对每条记录用 Pillow：
         - 复制底图
         - 把二维码图片缩放到 "二维码"图层 bbox 大小后贴上去
         - 在"名字" bbox 写 姓名（这里使用拼音作为姓名，沿用 txt 第 3 列）
         - 在"手机号" bbox 写 手机号
         - 输出为 PNG

字体：
    微软雅黑 Regular（C:\\Windows\\Fonts\\msyh.ttc）

依赖：
    pip install psd-tools pillow

运行（PowerShell，从脚本所在目录）：
    cd D:\\桌面文件\\新建文件夹\\飞书自动化
    python .\\生成个人码3.py
"""

import os
import time
from typing import Optional, Tuple

from psd_tools import PSDImage
from PIL import Image, ImageDraw, ImageFont


# ────────────────────────── 基础配置（按需修改） ──────────────────────────
PSD_PATH    = r"C:\Users\LENOVO\Desktop\模板1.psd"
TXT_PATH    = r"C:\Users\LENOVO\Desktop\读取后的内容.txt"
OUTPUT_DIR  = r"C:\Users\LENOVO\Desktop\二维码输出"
# 备份目录：每张生成后会同步再保存一份到这里。
# 置空字符串 "" 可关闭备份；该目录不可用（如 X 盘未挂载）时仅警告，不影响主输出。
BACKUP_DIR  = r"X:\backup\合伙宝妈个人码"

# PSD 模板里要替换的图层名（必须完全一致）
LAYER_QR    = "二维码"
LAYER_NAME  = "名字"
LAYER_PHONE = "手机号"

# ── 字体配置 ────────────────────────────────────────────────────────────
# 是否优先使用 PSD 文本图层里"原本的字体"（从 engine_dict 读出 PostScript 名，
# 再查 PSD_FONT_MAP 映射到本机字体文件）。
# True  → 优先 PSD 字体；找不到时回落到下面的 FALLBACK_FONT_PATH
# False → 始终使用 FALLBACK_FONT_PATH
USE_PSD_FONT = True

# PostScript 字体名 → 本机字体文件名（位于 C:\Windows\Fonts\）
# 找不到对应映射时会打印日志并回落，请按需补充
PSD_FONT_MAP = {
    # 微软雅黑系列（PSD 里最常见）
    "MicrosoftYaHei":          "msyh.ttc",
    "MicrosoftYaHei-Bold":     "msyhbd.ttc",
    "MicrosoftYaHeiUI":        "msyh.ttc",
    "MicrosoftYaHeiUI-Bold":   "msyhbd.ttc",
    "MicrosoftYaHeiLight":     "msyhl.ttc",
    "MicrosoftYaHeiUILight":   "msyhl.ttc",
    # 苹方（macOS 设计的 PSD 常见，Win 上一般没有，自动回落）
    "PingFangSC-Regular":      "msyh.ttc",
    "PingFangSC-Medium":       "msyhbd.ttc",
    "PingFangSC-Semibold":     "msyhbd.ttc",
    "PingFangSC-Bold":         "msyhbd.ttc",
    # 黑体 / 宋体 / 仿宋 / 楷体
    "SimHei":                  "simhei.ttf",
    "SimSun":                  "simsun.ttc",
    "NSimSun":                 "simsun.ttc",
    "FangSong":                "simfang.ttf",
    "KaiTi":                   "simkai.ttf",
    # 思源黑体 / 思源宋体
    "SourceHanSansCN-Regular": "msyh.ttc",
    "SourceHanSansCN-Bold":    "msyhbd.ttc",
    "SourceHanSerifCN-Regular":"simsun.ttc",
    # 英文常用
    "ArialMT":                 "arial.ttf",
    "Arial-BoldMT":            "arialbd.ttf",
    "Helvetica":               "arial.ttf",
    "Helvetica-Bold":          "arialbd.ttf",
    "TimesNewRomanPSMT":       "times.ttf",
    "TimesNewRomanPS-BoldMT":  "timesbd.ttf",
}

# Windows 字体目录
FONT_DIR = r"C:\Windows\Fonts"

# 找不到 PSD 字体时使用的回落字体（默认 = 微软雅黑 Bold，加粗效果）
FALLBACK_FONT_PATH = r"C:\Windows\Fonts\msyhbd.ttc"
FALLBACK_FONT_INDEX = 0

FONT_COLOR      = (0, 0, 0)                      # 黑色 #000000
FALLBACK_SIZE   = 36                             # 字号兜底（PSD 也读不到时使用）
SIZE_FROM_BBOX  = 0.80                           # 用 bbox.height * 系数 近似字号

# 文本绘制锚点："lt" = 左侧 + 视觉顶端
TEXT_ANCHOR = "lt"

# 垂直对齐策略（True = 在 bbox 内垂直居中；False = 顶端对齐）
NAME_VERTICAL_CENTER  = True
PHONE_VERTICAL_CENTER = False

# 输出文件名模板（{base} 即 txt 第一列）
# 说明：当前统一使用 .jpg 格式，主输出与备份都会落成 .jpg
OUTPUT_NAME_FMT = "{base}.jpg"

# JPEG 保存质量（1-95，建议 90+；越高越清晰、文件越大）
JPEG_QUALITY = 92


# ────────────────────────── psd_tools 工具函数 ──────────────────────────
def find_layer(psd, name: str):
    """递归找指定名称的图层"""
    for layer in psd.descendants():
        if layer.name == name:
            return layer
    return None


def layer_bbox(layer) -> Tuple[int, int, int, int]:
    """返回 (left, top, width, height)"""
    left, top, right, bottom = layer.bbox
    return int(left), int(top), int(right - left), int(bottom - top)


def detect_font_name(layer) -> Optional[str]:
    """
    从 psd_tools 文本图层 engine_dict 读出 PostScript 字体名（如 "MicrosoftYaHei-Bold"）。
    读不到返回 None。
    """
    try:
        ed = getattr(layer, "engine_dict", None)
        rd = getattr(layer, "resource_dict", None)
        # PostScript 名一般在 ResourceDict.FontSet[idx].Name
        if rd is not None:
            font_set = rd.get("FontSet")
            if font_set:
                # 取第一个 RunArray 引用的 FontIndex；找不到就用 FontSet[0]
                font_idx = 0
                if ed is not None:
                    try:
                        font_idx = int(
                            ed["StyleRun"]["RunArray"][0]
                              ["StyleSheet"]["StyleSheetData"]
                              ["Font"]
                        )
                    except Exception:
                        font_idx = 0
                if 0 <= font_idx < len(font_set):
                    name = font_set[font_idx].get("Name")
                    if name:
                        return str(name).strip()
                # 兜底：取第一个
                return str(font_set[0].get("Name", "")).strip() or None
    except Exception:
        pass
    return None


def resolve_font_file(ps_name: Optional[str]) -> Tuple[str, int]:
    """
    把 PSD PostScript 字体名映射为本机 (字体文件路径, 索引)。
    找不到时回落到 FALLBACK_FONT_PATH。
    """
    if not USE_PSD_FONT or not ps_name:
        return FALLBACK_FONT_PATH, FALLBACK_FONT_INDEX

    file_name = PSD_FONT_MAP.get(ps_name)
    if file_name:
        full_path = os.path.join(FONT_DIR, file_name)
        if os.path.exists(full_path):
            return full_path, 0

    # 没在映射表里 / 文件不存在 → 打印一次日志方便补映射
    print(
        f"  [字体] 未识别到 PSD 字体 PostScript 名 {ps_name!r}，"
        f"已回落为 {FALLBACK_FONT_PATH}"
    )
    return FALLBACK_FONT_PATH, FALLBACK_FONT_INDEX


def detect_font_size(layer, fallback: int = FALLBACK_SIZE) -> int:
    """
    尝试从 psd_tools 文本图层 engine_dict 读出字号；
    读不到时用 bbox 高度 × SIZE_FROM_BBOX 近似；最后用 fallback。
    """
    # 尝试 1：engine_dict 中找 FontSize
    try:
        ed = getattr(layer, "engine_dict", None)
        if ed is not None:
            run = ed["StyleRun"]["RunArray"][0]
            size = run["StyleSheet"]["StyleSheetData"]["FontSize"]
            if size:
                return max(8, int(round(float(size))))
    except Exception:
        pass

    # 尝试 2：用 bbox 高度近似
    try:
        _, _, _, h = layer_bbox(layer)
        if h > 0:
            return max(8, int(h * SIZE_FROM_BBOX))
    except Exception:
        pass

    return fallback


def composite_without_layers(psd, exclude_names: set) -> Image.Image:
    """
    渲染 PSD 模板为底图，但排除 exclude_names 中的图层。

    关键：必须显式指定 viewport = 整张 PSD 画布，
    否则 psd_tools 默认会按"剩余可见图层的边界框"自动裁剪，
    导致底图尺寸 / 坐标原点与 layer.bbox 不一致，
    后续用 layer.bbox 的坐标贴 QR / 写文字时就会出现明显位置偏差。
    """
    # 整张画布的 viewport：(left, top, right, bottom)
    full_viewport = (0, 0, psd.width, psd.height)

    def _filter(layer):
        return layer.is_visible() and layer.name not in exclude_names

    # 尝试 layer_filter + viewport（psd-tools 较新版本支持）
    try:
        return psd.composite(
            viewport=full_viewport,
            layer_filter=_filter,
        ).convert("RGBA")
    except TypeError:
        pass

    # 兼容老版本：手工切 visible，然后强制 viewport
    targets = [layer for layer in psd.descendants() if layer.name in exclude_names]
    saved = [t.visible for t in targets]
    try:
        for t in targets:
            t.visible = False
        try:
            return psd.composite(viewport=full_viewport).convert("RGBA")
        except TypeError:
            # 极老版本不支持 viewport，退化为整体 composite 后再 pad/crop 到画布
            img = psd.composite().convert("RGBA")
            if img.size != (psd.width, psd.height):
                canvas = Image.new("RGBA", (psd.width, psd.height), (0, 0, 0, 0))
                # 兜底假设左上角对齐
                canvas.paste(img, (0, 0))
                img = canvas
            return img
    finally:
        for t, v in zip(targets, saved):
            t.visible = v


# ────────────────────────── 文本自适应工具 ──────────────────────────
def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    """
    计算给定字体下文本的像素宽度。
    优先使用 font.getbbox（Pillow 8+），兼容老版本 getsize。
    """
    try:
        left, _, right, _ = font.getbbox(text)
        return int(right - left)
    except Exception:
        try:
            return int(font.getsize(text)[0])
        except Exception:
            return len(text) * font.size  # 极端兜底


def fit_font_to_width(
    font_path: str,
    font_index: int,
    text: str,
    max_width: int,
    base_size: int,
    min_size: int = 10,
) -> ImageFont.FreeTypeFont:
    """
    自适应字号：
      - 先按 base_size 加载字体；
      - 若文本宽度超过 max_width，则按比例缩小字号，直到放得下或到达 min_size；
      - 返回最终的字体对象。
    这样可以避免长拼音（如 zhangwenchong）超出 bbox 被截断。
    """
    size = max(min_size, int(base_size))
    font = ImageFont.truetype(font_path, size, index=font_index)
    if max_width <= 0 or not text:
        return font

    w = _text_width(font, text)
    if w <= max_width:
        return font

    # 按比例预估一个目标字号，再做一轮微调，避免循环过多
    target = max(min_size, int(size * max_width / max(w, 1)))
    font = ImageFont.truetype(font_path, target, index=font_index)
    while target > min_size and _text_width(font, text) > max_width:
        target -= 1
        font = ImageFont.truetype(font_path, target, index=font_index)
    return font


# ────────────────────────── 单条处理 ──────────────────────────
def render_one(
    base: Image.Image,
    qr_box: Tuple[int, int, int, int],
    name_box: Tuple[int, int, int, int],
    phone_box: Tuple[int, int, int, int],
    name_font: ImageFont.FreeTypeFont,
    phone_font: ImageFont.FreeTypeFont,
    qr_path: str,
    name_text: str,
    phone_text: str,
    name_font_path: str = "",
    name_font_idx: int = 0,
    name_base_size: int = 0,
    phone_font_path: str = "",
    phone_font_idx: int = 0,
    phone_base_size: int = 0,
) -> Image.Image:
    """复制底图 → 贴二维码 → 写姓名 / 手机号 → 返回新图"""
    canvas = base.copy()

    # 1. 贴二维码
    if not os.path.exists(qr_path):
        raise FileNotFoundError(f"二维码文件不存在：{qr_path}")
    qr_img = Image.open(qr_path).convert("RGBA")
    qr_resized = qr_img.resize((qr_box[2], qr_box[3]), Image.NEAREST)
    # 用自身 alpha 作为蒙版（如果是纯 RGB 也无影响）
    canvas.paste(qr_resized, (qr_box[0], qr_box[1]), qr_resized)

    # 2. 绘制文字
    draw = ImageDraw.Draw(canvas)

    # 2.1 若传入了字体路径与基准字号，则按 bbox 宽度自适应缩小字号，
    #     避免长拼音（如 zhangwenchong）写出后被裁掉。
    if name_font_path and name_base_size > 0:
        name_font = fit_font_to_width(
            name_font_path, name_font_idx,
            name_text, name_box[2], name_base_size,
        )
    if phone_font_path and phone_base_size > 0:
        phone_font = fit_font_to_width(
            phone_font_path, phone_font_idx,
            phone_text, phone_box[2], phone_base_size,
        )

    # 名字：根据 NAME_VERTICAL_CENTER 决定 顶端对齐 还是 bbox 内垂直居中
    if NAME_VERTICAL_CENTER:
        # 锚点改为 "lm"（左 + 视觉中线）；y 取 bbox 垂直中点
        name_y = name_box[1] + name_box[3] / 2
        draw.text(
            (name_box[0], name_y),
            name_text,
            font=name_font,
            fill=FONT_COLOR,
            anchor="lm",
        )
    else:
        draw.text(
            (name_box[0], name_box[1]),
            name_text,
            font=name_font,
            fill=FONT_COLOR,
            anchor=TEXT_ANCHOR,
        )

    # 手机号：同样按 PHONE_VERTICAL_CENTER 控制
    if PHONE_VERTICAL_CENTER:
        phone_y = phone_box[1] + phone_box[3] / 2
        draw.text(
            (phone_box[0], phone_y),
            phone_text,
            font=phone_font,
            fill=FONT_COLOR,
            anchor="lm",
        )
    else:
        draw.text(
            (phone_box[0], phone_box[1]),
            phone_text,
            font=phone_font,
            fill=FONT_COLOR,
            anchor=TEXT_ANCHOR,
        )

    return canvas


# ────────────────────────── 主流程 ──────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 备份目录：尝试创建。X 盘没挂载等情况下给一次性警告即可，主流程不受影响
    backup_ready = False
    if BACKUP_DIR:
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            backup_ready = True
        except Exception as be:
            print(
                f"[警告] 无法创建备份目录 {BACKUP_DIR}："
                f"{type(be).__name__}: {be}；本次将跳过备份"
            )

    print("=" * 60)
    print("批量生成个人码（psd_tools + Pillow）")
    print(f"  PSD 模板    = {PSD_PATH}")
    print(f"  数据源      = {TXT_PATH}")
    print(f"  导出目录    = {OUTPUT_DIR}")
    print(f"  备份目录    = {BACKUP_DIR or '(未启用)'}{'' if backup_ready or not BACKUP_DIR else ' [不可用]'}")
    print(f"  使用 PSD 字体 = {USE_PSD_FONT}")
    print(f"  字体回落    = {FALLBACK_FONT_PATH} (index={FALLBACK_FONT_INDEX})")
    print(f"  字体颜色    = {FONT_COLOR}")
    print("=" * 60)

    # 1. 加载 PSD + 定位图层
    print(f"[加载] 打开 PSD ...")
    psd = PSDImage.open(PSD_PATH)

    qr_layer    = find_layer(psd, LAYER_QR)
    name_layer  = find_layer(psd, LAYER_NAME)
    phone_layer = find_layer(psd, LAYER_PHONE)
    if qr_layer is None:
        raise RuntimeError(f"未找到图层：{LAYER_QR}")
    if name_layer is None:
        raise RuntimeError(f"未找到图层：{LAYER_NAME}")
    if phone_layer is None:
        raise RuntimeError(f"未找到图层：{LAYER_PHONE}")

    qr_box    = layer_bbox(qr_layer)
    name_box  = layer_bbox(name_layer)
    phone_box = layer_bbox(phone_layer)

    # 字号 + 字体（优先从 PSD 文本图层读）
    name_size  = detect_font_size(name_layer)
    phone_size = detect_font_size(phone_layer)
    name_ps    = detect_font_name(name_layer)
    phone_ps   = detect_font_name(phone_layer)
    name_font_path,  name_font_idx  = resolve_font_file(name_ps)
    phone_font_path, phone_font_idx = resolve_font_file(phone_ps)

    print(f"  二维码 bbox  = {qr_box}")
    print(
        f"  名字   bbox  = {name_box}, 字号≈{name_size}, "
        f"PSD字体={name_ps!r} → {os.path.basename(name_font_path)}"
    )
    print(
        f"  手机号 bbox  = {phone_box}, 字号≈{phone_size}, "
        f"PSD字体={phone_ps!r} → {os.path.basename(phone_font_path)}"
    )

    # 2. 合成底图（排除三个目标图层）
    print(f"[合成] 渲染底图（排除 二维码 / 名字 / 手机号 ）...")
    base = composite_without_layers(psd, {LAYER_QR, LAYER_NAME, LAYER_PHONE})
    print(f"  底图尺寸 = {base.size}")

    # 3. 加载字体（按各自图层读到的 PSD 字体加载）
    name_font  = ImageFont.truetype(name_font_path,  name_size,  index=name_font_idx)
    phone_font = ImageFont.truetype(phone_font_path, phone_size, index=phone_font_idx)

    # 4. 读取数据
    with open(TXT_PATH, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    print(f"[加载] txt 共 {len(lines)} 条数据")

    # 5. 批量处理
    success = 0
    failed  = 0
    started = time.time()

    for i, line in enumerate(lines, start=1):
        try:
            parts = line.split(",")
            if len(parts) < 4:
                print(f"[{i}/{len(lines)}] ❌ 字段不足：{line}")
                failed += 1
                continue
            base_name, qr_path, name_text, phone_text = (
                parts[0].strip(),
                parts[1].strip(),
                parts[2].strip(),
                parts[3].strip(),
            )

            print(f"[{i}/{len(lines)}] {base_name}")
            canvas = render_one(
                base,
                qr_box, name_box, phone_box,
                name_font, phone_font,
                qr_path, name_text, phone_text,
                name_font_path=name_font_path,
                name_font_idx=name_font_idx,
                name_base_size=name_size,
                phone_font_path=phone_font_path,
                phone_font_idx=phone_font_idx,
                phone_base_size=phone_size,
            )

            out_name = OUTPUT_NAME_FMT.format(base=base_name)
            out_path = os.path.join(OUTPUT_DIR, out_name)
            # canvas 是 RGBA；JPG 不支持透明通道，必须先转 RGB 并把透明区域填白
            # 这里把转换结果复用，避免主输出和备份各转一次
            rgb_canvas = Image.new("RGB", canvas.size, (255, 255, 255))
            rgb_canvas.paste(canvas, mask=canvas.split()[3])  # 用 alpha 通道作为 mask
            rgb_canvas.save(out_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            print(f"  ✅ 已生成: {out_path}")

            # 额外保存一份到 BACKUP_DIR（仅当 backup_ready 时尝试）
            if BACKUP_DIR and backup_ready:
                try:
                    backup_path = os.path.join(BACKUP_DIR, out_name)
                    rgb_canvas.save(backup_path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                    print(f"  💾 已备份: {backup_path}")
                except Exception as be:
                    # 单条备份失败仅警告，不影响主流程
                    print(f"  ⚠️ 备份失败（{type(be).__name__}: {be}），已忽略")

            success += 1
        except Exception as e:
            print(f"  ❌ 失败：{type(e).__name__}: {e}")
            failed += 1
            continue

    elapsed = time.time() - started
    print("=" * 60)
    print(f"[完成] 成功 {success} / 失败 {failed} / 总计 {len(lines)}")
    print(f"[完成] 耗时 {elapsed:.2f}s，导出目录：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
