import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import openpyxl
import os
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import cv2

class ImageSimilarityApp:
    def __init__(self, root):
        self.root = root
        self.root.title("图片相似度查找工具")
        self.root.geometry("900x700")
        
        self.excel_path = r"C:\Users\LENOVO\Desktop\通讯录导出.xlsx"
        self.images = []
        self.query_image_path = None
        
        self.setup_ui()
        self.load_images()
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        ttk.Label(main_frame, text="图片相似度查找工具", font=("Arial", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        upload_frame = ttk.LabelFrame(main_frame, text="上传查询图片", padding="10")
        upload_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Button(upload_frame, text="选择图片", command=self.upload_image).grid(row=0, column=0, padx=(0, 10))
        self.query_label = ttk.Label(upload_frame, text="未选择图片")
        self.query_label.grid(row=0, column=1, sticky=tk.W)
        
        self.query_image_label = ttk.Label(upload_frame)
        self.query_image_label.grid(row=1, column=0, columnspan=2, pady=(10, 0))
        
        result_frame = ttk.LabelFrame(main_frame, text="相似图片结果", padding="10")
        result_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.result_text = tk.Text(result_frame, wrap=tk.WORD, height=15)
        self.result_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        scrollbar = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_text.yview)
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.result_text.configure(yscrollcommand=scrollbar.set)
        
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        
        ttk.Button(button_frame, text="查找相似图片", command=self.find_similar).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="清空结果", command=self.clear_results).pack(side=tk.LEFT)
        
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        self.status_label = ttk.Label(status_frame, text="准备就绪")
        self.status_label.pack()
    
    def load_images(self):
        self.status_label.config(text="正在加载图片...")
        self.root.update()
        
        try:
            wb = openpyxl.load_workbook(self.excel_path)
            ws = wb.active
            
            for row in ws.iter_rows(min_col=5, max_col=5):
                for cell in row:
                    if cell.value:
                        cell_value = str(cell.value).strip()
                        if cell_value and os.path.exists(cell_value):
                            self.images.append(cell_value)
            
            if not self.images:
                image_dir = r"C:\Users\LENOVO\Desktop\临时"
                if os.path.exists(image_dir):
                    for file in os.listdir(image_dir):
                        if file.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                            self.images.append(os.path.join(image_dir, file))
            
            self.status_label.config(text=f"已加载 {len(self.images)} 张图片")
        except Exception as e:
            self.status_label.config(text=f"加载失败: {str(e)}")
    
    def upload_image(self):
        file_path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("所有文件", "*.*")
            ]
        )
        
        if file_path:
            self.query_image_path = file_path
            self.query_label.config(text=os.path.basename(file_path))
            
            try:
                img = Image.open(file_path)
                img.thumbnail((200, 200))
                photo = ImageTk.PhotoImage(img)
                self.query_image_label.config(image=photo)
                self.query_image_label.image = photo
            except Exception as e:
                messagebox.showerror("错误", f"无法加载图片: {str(e)}")
    
    def extract_features(self, image_path):
        try:
            img = cv2.imread(image_path)
            if img is None:
                img = Image.open(image_path)
                img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            
            img = cv2.resize(img, (64, 64))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            features = img.flatten()
            return features
        except Exception as e:
            print(f"无法提取特征: {image_path}, 错误: {e}")
            return None
    
    def find_similar(self):
        if not self.query_image_path:
            messagebox.showwarning("警告", "请先上传查询图片")
            return
        
        if not self.images:
            messagebox.showwarning("警告", "没有可用的图片库")
            return
        
        self.status_label.config(text="正在查找相似图片...")
        self.root.update()
        
        query_features = self.extract_features(self.query_image_path)
        if query_features is None:
            messagebox.showerror("错误", "无法提取查询图片的特征")
            return
        
        similarities = []
        
        for img_path in self.images:
            img_features = self.extract_features(img_path)
            if img_features is not None:
                similarity = cosine_similarity([query_features], [img_features])[0][0]
                similarities.append((img_path, similarity))
        
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, f"查询图片: {os.path.basename(self.query_image_path)}\n")
        self.result_text.insert(tk.END, f"找到 {len(similarities)} 张图片进行比对\n")
        self.result_text.insert(tk.END, "="*60 + "\n\n")
        
        for i, (img_path, similarity) in enumerate(similarities[:10], 1):
            self.result_text.insert(tk.END, f"{i}. {os.path.basename(img_path)}\n")
            self.result_text.insert(tk.END, f"   路径: {img_path}\n")
            self.result_text.insert(tk.END, f"   相似度: {similarity:.4f}\n\n")
        
        self.status_label.config(text="查找完成")
    
    def clear_results(self):
        self.result_text.delete(1.0, tk.END)
        self.query_image_label.config(image="")
        self.query_image_label.image = None
        self.query_label.config(text="未选择图片")
        self.query_image_path = None
        self.status_label.config(text="准备就绪")

def main():
    root = tk.Tk()
    app = ImageSimilarityApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
