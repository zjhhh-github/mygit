# -*- coding: utf-8 -*-
"""
批量处理二维码图片：OpenCV 二维码专用处理
==============================================================

处理流程（针对二维码识别效果做了专门优化）：
    1. 读取图片（兼容含中文 / ¿ 等非 ASCII 字符的路径）
    2. 转灰度
    3. 高斯轻度降噪（去掉边缘锯齿和扫描噪点）
    4. OTSU 自适应二值化（不同光照 / 不同纸张都能稳定二值化）
    5. resize 至 TARGET_WIDTH x TARGET_HEIGHT，使用 INTER_NEAREST
       —— 避免插值让二维码模块"粗细不一 / 边缘模糊"
    6. 覆盖保存原图

特点：
    - 不依赖 Photoshop / COM
    - 二维码模块边界清晰、大小一致、可稳定识别
    - 单张失败不影响整批
    - 中文 / ¿ 等特殊路径完整支持

依赖：
    pip install opencv-python numpy

运行（PowerShell，从脚本所在目录）：
    cd D:\\桌面文件\\新建文件夹\\飞书自动化
    python .\\批量执行动作_二维码.py
"""

import os
import time
import numpy as np
import cv2


# ────────────────────────── 基础配置（按需修改） ──────────────────────────
INPUT_DIR = r"C:\Users\LENOVO\Desktop\保存的二维码"

# 支持的图片后缀（小写，不带点）
IMAGE_EXTS = {"jpg", "jpeg", "png"}

# 目标尺寸（强制拉伸到该尺寸；非正方形原图会被变形）
TARGET_WIDTH  = 1366
TARGET_HEIGHT = 1366

# 高斯模糊核大小（必须是奇数）。3 = 仅做轻度降噪，不会糊化模块边界。
GAUSSIAN_KSIZE = 3


# ────────────────────────── 工具函数 ──────────────────────────
def list_images(folder: str) -> list[str]:
    """列出文件夹内所有支持后缀的图片，返回绝对路径列表（已排序）"""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"目录不存在：{folder}")
    paths = []
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        if ext in IMAGE_EXTS:
            paths.append(os.path.abspath(full))
    paths.sort()
    return paths


def imread_unicode(path: str) -> np.ndarray | None:
    """
    兼容 Unicode 路径的图片读取。
    cv2.imread 在 Windows 下不能直接读含非 ASCII 字符的路径（会返回 None），
    用 numpy 二进制读 + cv2.imdecode 兜底。
    """
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size == 0:
        return None
    return cv2.imdecode(raw, cv2.IMREAD_COLOR)


def imwrite_unicode(path: str, img: np.ndarray) -> bool:
    """
    兼容 Unicode 路径的图片写入。
    根据文件扩展名编码为对应格式后再用 numpy 写到磁盘。
    """
    ext = os.path.splitext(path)[1]  # 含点：".png"
    if not ext:
        ext = ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    buf.tofile(path)
    return True


# ────────────────────────── 单张处理 ──────────────────────────
def process_one(image_path: str) -> None:
    """
    读 → 灰度 → 高斯降噪 → OTSU 二值化 → NEAREST resize → 覆盖保存
    """
    # 1. 读取图片（兼容 Unicode 路径）
    img = imread_unicode(image_path)
    if img is None:
        raise RuntimeError("无法读取图片（路径或文件可能损坏）")

    # 2. 转灰度
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 3. 轻度高斯降噪（去掉扫描噪点 / JPG 压缩噪点 / 边缘小毛刺）
    gray = cv2.GaussianBlur(gray, (GAUSSIAN_KSIZE, GAUSSIAN_KSIZE), 0)

    # 4. OTSU 自适应二值化
    #    - 阈值参数填 0，THRESH_OTSU 会自动算最优阈值
    #    - 适应不同光照 / 不同纸张色 / 不同打印浓度
    _, binary = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    # 5. resize 到目标尺寸：使用 INTER_NEAREST 保持模块边界清晰、不模糊
    binary = cv2.resize(
        binary,
        (TARGET_WIDTH, TARGET_HEIGHT),
        interpolation=cv2.INTER_NEAREST,
    )

    # 6. 覆盖保存（兼容 Unicode 路径）
    if not imwrite_unicode(image_path, binary):
        raise RuntimeError("图片编码 / 写入失败")


# ────────────────────────── 主流程 ──────────────────────────
def main():
    print("=" * 60)
    print("批量处理二维码图片：OpenCV 二维码专用处理")
    print(f"  目录       = {INPUT_DIR}")
    print(f"  目标尺寸   = {TARGET_WIDTH} x {TARGET_HEIGHT}")
    print(f"  高斯核     = ({GAUSSIAN_KSIZE}, {GAUSSIAN_KSIZE})")
    print(f"  阈值方法   = OTSU 自适应")
    print(f"  缩放方式   = cv2.INTER_NEAREST")
    print(f"  支持后缀   = {sorted(IMAGE_EXTS)}")
    print("=" * 60)

    images = list_images(INPUT_DIR)
    print(f"[扫描] 共 {len(images)} 张图片")
    if not images:
        print("[退出] 没有可处理的图片")
        return

    success = 0
    failed  = 0
    started = time.time()

    for idx, path in enumerate(images, start=1):
        name = os.path.basename(path)
        print(f"[{idx}/{len(images)}] {name}")
        try:
            process_one(path)
            print(f"  ✅ 成功")
            success += 1
        except Exception as e:
            print(f"  ❌ 失败：{type(e).__name__}: {e}")
            failed += 1
            continue

    elapsed = time.time() - started
    print("=" * 60)
    print(f"[完成] 成功 {success} / 失败 {failed} / 总计 {len(images)}")
    print(f"[完成] 耗时 {elapsed:.2f}s")


if __name__ == "__main__":
    main()
