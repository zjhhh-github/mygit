import os
import numpy as np
from PIL import Image
import argparse

# 图片相似度查找工具
# 功能：在一个文件夹中查找与输入图片相似的图片
# 步骤：1. 移除输入图片的白色边框  2. 将图片调整为58x58像素  3. 计算相似度  4. 返回最相似的图片


def remove_white_borders(image):
    """
    从图像中移除白色边框，通过查找非白色内容的边界框来实现
    """
    # 如果是PIL图像，则转换为numpy数组
    if isinstance(image, Image.Image):
        img_array = np.array(image)
    else:
        img_array = image

    # 处理不同的图像模式（RGB、RGBA等）
    if len(img_array.shape) == 3:
        # 对于彩色图像（高度，宽度，通道数）
        # 查找不是完全白色的行和列
        # 检查像素是否不是白色（255, 255, 255）或不是接近白色
        non_white_rows = np.where(~np.all(img_array >= 240, axis=1))[0]
        non_white_cols = np.where(~np.all(img_array >= 240, axis=0))[0]
    else:
        # 对于灰度图像
        non_white_rows = np.where(img_array < 240)[0]
        non_white_cols = np.where(np.any(img_array < 240, axis=0))[0]

    if non_white_rows.size == 0 or non_white_cols.size == 0:
        # 如果整个图像都是白色，则返回原图
        return image

    # 计算边界框
    row_min, row_max = non_white_rows.min(), non_white_rows.max()
    col_min, col_max = non_white_cols.min(), non_white_cols.max()

    # 根据边界框裁剪图像
    cropped_array = img_array[row_min:row_max+1, col_min:col_max+1]

    # 如果原始图像是PIL图像，则转换回PIL图像
    if isinstance(image, Image.Image):
        return Image.fromarray(cropped_array)
    
    return cropped_array


def resize_image_to_58x58(image):
    """
    将图像调整为58x58像素
    """
    # 如果是numpy数组，则转换为PIL图像
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    
    # 使用高质量重采样将图像调整为58x58
    # 对旧版本PIL使用ANTIALIAS，对新版本使用LANCZOS
    try:
        resized_image = image.resize((132, 132), Image.LANCZOS)
    except AttributeError:
        # 旧版本PIL的备用方案
        resized_image = image.resize((132, 132), Image.ANTIALIAS)
    
    return resized_image


def calculate_image_similarity(img1, img2):
    """
    使用均方误差计算两张图像的相似度
    返回0（完全不同）到1（完全相同）之间的值
    MSE越小表示相似度越高
    """
    # 如果是PIL图像，则转换为numpy数组
    if isinstance(img1, Image.Image):
        img1 = np.array(img1)
    if isinstance(img2, Image.Image):
        img2 = np.array(img2)
    
    # 确保两张图像具有相同的形状
    if img1.shape != img2.shape:
        # 如需要，调整img2以匹配img1的尺寸
        if len(img1.shape) == 3 and len(img2.shape) == 3:
            img2 = np.array(Image.fromarray(img2).resize((img1.shape[1], img1.shape[0])))
        elif len(img1.shape) == 2 and len(img2.shape) == 2:
            img2 = np.array(Image.fromarray(img2).resize((img1.shape[1], img1.shape[0])))
        else:
            # 如果通道维度不同，都转换为RGB
            if len(img1.shape) == 3:
                img1 = img1[:, :, :3]  # 只取RGB通道
            if len(img2.shape) == 3:
                img2 = img2[:, :, :3]  # 只取RGB通道
    
    # 计算均方误差
    mse = np.mean((img1 - img2) ** 2)
    
    # 将MSE转换为相似度分数（0到1）
    # 8位图像的最大可能MSE为255^2 = 65025
    max_mse = 255 ** 2
    similarity = 1 - (mse / max_mse)
    
    # 确保相似度在0和1之间
    return max(0, min(1, similarity))


def find_similar_images(input_image_path, folder_path, threshold=0.7, top_n=None):
    """
    在文件夹中查找与输入图像相似的图像
    
    参数:
        input_image_path: 输入图像的路径
        folder_path: 包含待比较图像的文件夹路径
        threshold: 相似度阈值（0-1），只返回高于此阈值的图像
        top_n: 返回最相似图像的数量（None表示返回所有高于阈值的图像）
    
    返回:
        包含(图像路径, 相似度分数)元组的列表，按相似度分数降序排列
    """
    # 加载并预处理输入图像
    input_img = Image.open(input_image_path).convert('RGB')
    
    # 移除白色边框
    input_img_no_border = remove_white_borders(input_img)
    
    # 如需要，转换回PIL图像
    if isinstance(input_img_no_border, np.ndarray):
        input_img_no_border = Image.fromarray(input_img_no_border)
    
    # 调整大小为58x58
    input_img_processed = resize_image_to_58x58(input_img_no_border)
    
    # 支持的图像扩展名
    supported_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif')
    
    # 获取文件夹中的所有图像文件
    # similarities = []
    # try:
    #     # 加载并处理比较图像
    #     comp_img = Image.open(folder_path).convert('RGB')
        
    #     # 移除白色边框
    #     comp_img_no_border = remove_white_borders(comp_img)
        
    #     # 如需要，转换回PIL图像
    #     if isinstance(comp_img_no_border, np.ndarray):
    #         comp_img_no_border = Image.fromarray(comp_img_no_border)
        
    #     # 调整大小为58x58
    #     comp_img_processed = resize_image_to_58x58(comp_img_no_border)
        
    #     # 计算相似度
    #     similarity = calculate_image_similarity(input_img_processed, comp_img_processed)
        
    #     if similarity >= threshold:
    #         similarities.append((folder_path, similarity))
            
    #     # 打印处理进度
    #     # print(f"已处理 {idx+1}/{total_files} 张图片...")
        
    # except Exception as e:
    #     print(f"处理 {folder_path} 时出错: {str(e)}")
        
    # return similarities





    image_files = []
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(supported_extensions):
            image_files.append(os.path.join(folder_path, filename))
    
    # 计算与文件夹中每张图像的相似度
    similarities = []
    total_files = len(image_files)
    
    for idx, image_file in enumerate(image_files):
        try:
            # 加载并处理比较图像
            comp_img = Image.open(image_file).convert('RGB')
            
            # 移除白色边框
            comp_img_no_border = remove_white_borders(comp_img)
            
            # 如需要，转换回PIL图像
            if isinstance(comp_img_no_border, np.ndarray):
                comp_img_no_border = Image.fromarray(comp_img_no_border)
            
            # 调整大小为58x58
            comp_img_processed = resize_image_to_58x58(comp_img_no_border)
            
            # 计算相似度
            similarity = calculate_image_similarity(input_img_processed, comp_img_processed)
            
            if similarity >= threshold:
                similarities.append((image_file, similarity))
                
            # 打印处理进度
            print(f"已处理 {idx+1}/{total_files} 张图片...")
            
        except Exception as e:
            print(f"处理 {image_file} 时出错: {str(e)}")
            continue
    
    # 按相似度分数排序（降序）
    similarities.sort(key=lambda x: x[1], reverse=True)
    
    # 如指定了top_n，则返回前N个结果
    if top_n:
        return similarities[:top_n]
    
    return similarities


def main():
    parser = argparse.ArgumentParser(description='Find similar images in a folder.')
    parser.add_argument('input_image', help='Path to the input image')
    parser.add_argument('folder', help='Path to the folder containing images to compare')
    parser.add_argument('--threshold', type=float, default=0.3, help='Similarity threshold (default: 0.3)')
    parser.add_argument('--top-n', type=int, help='Return top N most similar images')
    
    args = parser.parse_args()
    
    print(f"Processing input image: {args.input_image}")
    print(f"Comparing with images in folder: {args.folder}")
    print(f"Threshold: {args.threshold}")
    
    similar_images = find_similar_images(
        args.input_image, 
        args.folder, 
        threshold=args.threshold,
        top_n=args.top_n
    )
    
    print("\nTop similar images:")
    for image_path, similarity in similar_images:
        print(f"{image_path}: {similarity:.4f}")


if __name__ == "__main__":
    # 简化的硬编码路径使用
    input_image_path = r"C:\Users\LENOVO\Desktop\微信图片_20260105164106_9_270.png"  # 输入图像路径
    folder_path = r"C:\Users\LENOVO\Desktop\临时"       # 待搜索的文件夹路径
    threshold = 0.3  # 相似度阈值
    top_n = 10       # 返回最相似的图片数量
    
    print(f"处理输入图像: {input_image_path}")
    print(f"与文件夹中的图像进行比较: {folder_path}")
    print(f"相似度阈值: {threshold}")
    
    # 查找相似图像
    similar_images = find_similar_images(input_image_path, folder_path, threshold=threshold, top_n=top_n)
    
    print(f"\n最相似的 {top_n} 张图像:")
    for image_path, similarity in similar_images:
        print(f"{image_path}: {similarity:.4f}")