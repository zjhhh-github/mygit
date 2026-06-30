import win32com.client
import os
import re

PSD_PATH = r"C:\Users\LENOVO\Desktop\模板1.psd"
OUTPUT_DIR = r"C:\Users\LENOVO\Desktop\生成结果"

os.makedirs(OUTPUT_DIR, exist_ok=True)

ps = win32com.client.Dispatch("Photoshop.Application")
ps.Visible = False  # 不弹界面


# ===== 工具：清洗文件名非法字符（Windows 不允许 \ / : * ? " < > |） =====
def sanitize_filename(name: str) -> str:
    """去掉 Windows 文件名非法字符，返回清洗后的字符串"""
    return re.sub(r'[\\/:*?"<>|]', '', name)


# ===== 工具：把图片路径规范化（绝对路径 + 反斜杠 + 存在性检查） =====
def normalize_image_path(path: str) -> str:
    """
    返回 Photoshop 可识别的绝对 Windows 路径。
    - 转绝对路径
    - 把 / 统一替换为 \\
    - 校验文件存在，否则抛 FileNotFoundError
    """
    abs_path = os.path.abspath(path)
    abs_path = abs_path.replace("/", "\\")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"二维码文件不存在: {abs_path}")
    return abs_path


# ===== 工具：以 Place（嵌入）方式把图片放入当前 PSD =====
def place_image(ps_app, doc, image_path: str) -> None:
    """
    用 Photoshop 的 "Place" 命令把图片作为一个新的智能对象图层
    嵌入到当前 PSD 中，避免 ps.Open + Copy/Paste 触发的弹窗
    （颜色配置 / 嵌入式配置文件 / 用户取消等）。

    动作等价于菜单：文件 → 置入嵌入对象。
    新图层会被插入在当前 ActiveLayer 之上，并自动成为 ActiveLayer。
    """
    ps_app.ActiveDocument = doc

    desc = win32com.client.Dispatch("Photoshop.ActionDescriptor")
    desc.PutPath(ps_app.CharIDToTypeID("null"), image_path)
    # 第三个参数 3 = DialogModes.DisplayNoDialogs，不弹任何对话框
    ps_app.ExecuteAction(ps_app.CharIDToTypeID("Plc "), desc, 3)


# ===== 查找图层（递归）=====
def find_layer(layers, name):
    for layer in layers:
        if layer.Name == name:
            return layer
        if layer.typename == "LayerSet":  # 组
            result = find_layer(layer.Layers, name)
            if result:
                return result
    return None


# ===== 替换二维码（方式1：直接替换内容）=====
def replace_image(layer, image_path):
    # 选中图层
    ps.ActiveDocument.ActiveLayer = layer

    # 替换内容（智能对象才有效）
    ps.ExecuteAction(
        ps.StringIDToTypeID("placedLayerReplaceContents"),
        ps.ActionDescriptor(),
        3
    )


# ===== 修改文本 =====
def set_text(layer, text):
    layer.TextItem.Contents = text


# ===== 处理一条数据 =====
def process_one(qr_path, pinyin_name, phone, index):
    doc = ps.Open(PSD_PATH)

    # 找图层
    qr_layer = find_layer(doc.Layers, "二维码")
    name_layer = find_layer(doc.Layers, "名字")
    phone_layer = find_layer(doc.Layers, "手机号")

    if not all([qr_layer, name_layer, phone_layer]):
        raise Exception("图层名称不匹配")

    # 替换内容
    set_text(name_layer, pinyin_name)
    set_text(phone_layer, phone)

    # 👉 替换二维码（用 Place 嵌入，避免 ps.Open 触发弹窗 → "用户取消了操作"）
    qr_path_norm = normalize_image_path(qr_path)
    print(f"正在置入二维码: {qr_path_norm}")
    ps.ActiveDocument.ActiveLayer = qr_layer  # 让新图层放在二维码层之上
    place_image(ps, doc, qr_path_norm)
    # Place 完成后新图层会成为 ActiveLayer，给它改个名便于识别
    doc.ActiveLayer.Name = "二维码_替换"

    # 导出
    output_path = os.path.join(OUTPUT_DIR, f"{index}.jpg")

    jpg_options = win32com.client.Dispatch("Photoshop.JPEGSaveOptions")
    jpg_options.Quality = 12

    doc.SaveAs(output_path, jpg_options)
    doc.Close(2)

    print(f"✅ 已生成: {output_path}")


# ===== 示例调用 =====
if __name__ == "__main__":
    # 这里换成你从飞书读到的数据
    with open(r"C:\Users\LENOVO\Desktop\读取后的内容.txt", "r", encoding="utf-8") as f:
        data = [line.strip().split(",") for line in f.readlines()]
    data = [{"qr": line[1], "pinyin": line[2], "phone": line[3]} for line in data]

    # 主循环：单条出错不影响整体批处理
    success_count = 0
    fail_count = 0
    for i, d in enumerate(data, 1):
        try:
            process_one(d["qr"], d["pinyin"], d["phone"], i)
            success_count += 1
        except Exception as e:
            print(f"❌ 第{i}条处理失败: {e}")
            # 出错后尽量关掉残留的 Photoshop 文档，避免下一条受污染
            try:
                while ps.Documents.Count > 0:
                    ps.ActiveDocument.Close(2)  # 2 = 不保存
            except Exception:
                pass
            fail_count += 1
            continue

    print("=" * 50)
    print(f"批处理完成：成功 {success_count} / 失败 {fail_count} / 总计 {len(data)}")
