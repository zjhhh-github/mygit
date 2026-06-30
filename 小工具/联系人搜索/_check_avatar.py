import io, urllib.request
from PIL import Image, ImageTk
import tkinter as tk

url = "https://wx.qlogo.cn/mmhead/ver_1/CQ6iccyftm4IDkZvnCQ1fuxnyw6LibE6ElJMmibzH2L10QqJoLzTdkxLMrMtuqb05thNnwsGOcnjrmbmP2scTy31BkFvfd1OBWsLgeEX11yd298KD3r1p04zASVJ28QIltzGpxMa3Xme1tpic2rSzO8fmg/132"

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=8) as resp:
    data = resp.read()

img = Image.open(io.BytesIO(data)).convert("RGBA")
img = img.resize((40, 40), Image.LANCZOS)

root = tk.Tk()
photo = ImageTk.PhotoImage(img)
tk.Label(root, image=photo).pack()
root.mainloop()
