"""
生成默认图标文件
创建一个简单的通讯录图标（联系人卡片样式）
"""
from PIL import Image, ImageDraw

def create_icon():
    """创建一个简单的通讯录图标"""
    # 创建256x256的图像（高分辨率）
    size = 256
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 背景圆形（蓝色渐变效果）
    margin = 20
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(52, 152, 219, 255),  # 蓝色 #3498db
        outline=(41, 128, 185, 255),  # 深蓝色边框
        width=8
    )
    
    # 绘制联系人卡片图标
    card_margin = 60
    card_top = 70
    card_bottom = size - 70
    card_left = card_margin
    card_right = size - card_margin
    
    # 白色卡片背景
    draw.rounded_rectangle(
        [card_left, card_top, card_right, card_bottom],
        radius=15,
        fill=(255, 255, 255, 255),
        outline=(236, 240, 241, 255),
        width=3
    )
    
    # 绘制联系人头像（圆形）
    avatar_center_x = size // 2
    avatar_center_y = 115
    avatar_radius = 25
    draw.ellipse(
        [avatar_center_x - avatar_radius, avatar_center_y - avatar_radius,
         avatar_center_x + avatar_radius, avatar_center_y + avatar_radius],
        fill=(52, 152, 219, 255),  # 蓝色
        outline=(41, 128, 185, 255),
        width=2
    )
    
    # 绘制信息行（3条横线）
    line_color = (189, 195, 199, 255)  # 灰色
    line_width = 4
    line_spacing = 20
    line_start_y = 160
    
    for i in range(3):
        y = line_start_y + i * line_spacing
        line_left = card_left + 25
        line_right = card_right - 25
        if i == 0:
            line_right = card_right - 60  # 第一行短一些
        draw.rounded_rectangle(
            [line_left, y, line_right, y + line_width],
            radius=2,
            fill=line_color
        )
    
    # 保存为多种尺寸的 .ico 文件
    icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save('icon.ico', format='ICO', sizes=icon_sizes)
    print("OK - Icon generated: icon.ico")
    print(f"  Sizes: {', '.join([f'{w}x{h}' for w, h in icon_sizes])}")

if __name__ == "__main__":
    try:
        create_icon()
    except ImportError:
        print("✗ 缺少 Pillow 库，正在安装...")
        import subprocess
        subprocess.check_call(['python', '-m', 'pip', 'install', 'pillow'])
        print("✓ Pillow 安装完成，重新生成图标...")
        create_icon()
