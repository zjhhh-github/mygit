import os
from PIL import Image
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import time

def extract_features(image_path, size=(64, 64)):
    img = Image.open(image_path).convert('RGB').resize(size)
    img_array = np.array(img)
    features = img_array.flatten()
    return features

def find_similar_images(target_image_path, folder_path, top_k=10, output_file=None):
    output = []
    output.append(f"目标图片: {target_image_path}")
    output.append(f"搜索文件夹: {folder_path}")
    output.append(f"查找最相似的 {top_k} 张图片\n")
    
    target_features = extract_features(target_image_path)
    
    similarities = []
    
    image_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
    total_files = len(image_files)
    
    output.append(f"找到 {total_files} 张图片，开始处理...\n")
    
    for idx, filename in enumerate(image_files, 1):
        image_path = os.path.join(folder_path, filename)
        
        try:
            features = extract_features(image_path)
            similarity = cosine_similarity([target_features], [features])[0][0]
            similarities.append((filename, similarity))
            
            if idx % 100 == 0:
                output.append(f"已处理 {idx}/{total_files} 张图片...")
                
        except Exception as e:
            output.append(f"处理 {filename} 时出错: {e}")
    
    similarities.sort(key=lambda x: x[1], reverse=True)
    
    output.append(f"\n处理完成！找到 {len(similarities)} 张图片\n")
    output.append("=" * 80)
    output.append(f"最相似的 {top_k} 张图片:")
    output.append("=" * 80)
    
    for i, (filename, similarity) in enumerate(similarities[:top_k], 1):
        output.append(f"{i}. {filename}")
        output.append(f"   相似度: {similarity:.4f}")
        output.append(f"   路径: {os.path.join(folder_path, filename)}")
        output.append("")
    
    result_text = "\n".join(output)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result_text)
        print(f"结果已保存到: {output_file}")
    else:
        print(result_text)
    
    return similarities[:top_k]

if __name__ == "__main__":
    start_time = time.time()
    print(start_time)
    target_image = r"C:\Users\LENOVO\Desktop\头像.png"
    folder_path = r"C:\Users\LENOVO\Desktop\临时"
    output_file = r"D:\桌面文件\新建文件夹\test\结构图\similarity_results.txt"
    
    find_similar_images(target_image, folder_path, top_k=20, output_file=output_file)
    end_time = time.time()
    print(end_time)
    print(f"耗时: {end_time - start_time:.2f} 秒")
