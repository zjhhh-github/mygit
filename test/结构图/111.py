import openpyxl
import os
from PIL import Image
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

class ImageSimilarityFinder:
    def __init__(self, excel_path):
        self.excel_path = excel_path
        self.images = []
        self.load_images_from_excel()
    
    def load_images_from_excel(self):
        try:
            wb = openpyxl.load_workbook(self.excel_path)
            ws = wb.active
            
            for row in ws.iter_rows(min_col=5, max_col=5):
                for cell in row:
                    if cell.value:
                        cell_value = str(cell.value).strip()
                        if cell_value:
                            if os.path.exists(cell_value):
                                self.images.append(cell_value)
                                print(f"找到图片路径: {cell_value}")
                            elif cell_value.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                                print(f"可能的图片路径（文件不存在）: {cell_value}")
        except Exception as e:
            print(f"读取Excel文件时出错: {e}")
        
        print(f"\n总共找到 {len(self.images)} 张图片")
    
    def extract_features(self, image_path):
        try:
            img = Image.open(image_path)
            img = img.convert('L')
            img = img.resize((64, 64))
            features = np.array(img).flatten()
            return features
        except Exception as e:
            print(f"无法提取特征: {image_path}, 错误: {e}")
            return None
    
    def find_similar_images(self, query_image_path, top_k=5):
        query_features = self.extract_features(query_image_path)
        if query_features is None:
            return []
        
        similarities = []
        
        for img_path in self.images:
            img_features = self.extract_features(img_path)
            if img_features is not None:
                similarity = cosine_similarity([query_features], [img_features])[0][0]
                similarities.append((img_path, similarity))
        
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]

def main():
    excel_path = r"C:\Users\LENOVO\Desktop\通讯录导出.xlsx"
    query_image_path = r"C:\Users\LENOVO\Desktop\wechat_2025-12-28_143600_098.png"
    
    print("正在加载Excel文件中的图片...")
    finder = ImageSimilarityFinder(excel_path)
    
    if not finder.images:
        print("\n未找到图片，请检查E列是否包含图片路径")
        print("尝试从media目录加载图片...")
        
        image_dir = r"C:\Users\LENOVO\Desktop\media"
        if os.path.exists(image_dir):
            print(f"\n从目录加载图片: {image_dir}")
            for file in os.listdir(image_dir):
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                    finder.images.append(os.path.join(image_dir, file))
            print(f"从目录加载了 {len(finder.images)} 张图片")
    
    if finder.images:
        print("\n可用的图片:")
        for i, img in enumerate(finder.images, 1):
            print(f"{i}. {os.path.basename(img)}")
        
        if os.path.exists(query_image_path):
            print(f"\n正在查找与 '{os.path.basename(query_image_path)}' 相似的图片...")
            similar_images = finder.find_similar_images(query_image_path)
            
            if similar_images:
                print("\n最相似的图片:")
                for i, (img_path, similarity) in enumerate(similar_images, 1):
                    print(f"{i}. {os.path.basename(img_path)} - 相似度: {similarity:.4f}")
                    print(f"   路径: {img_path}")
            else:
                print("未找到相似图片")
        else:
            print(f"图片路径不存在: {query_image_path}")
    else:
        print("没有找到任何图片")

if __name__ == "__main__":
    main()
