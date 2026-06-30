import os
import requests
from openpyxl import load_workbook

# ====== 配置区 ======
APP_ID = "cli_a96f36ed1538dbcf"
APP_SECRET = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"

APP_TOKEN = "Zk05bwki2abD8XsBBOccaFsPn8e"
TABLE_ID = "tblKa8wryhV4d7F4"

EXCEL_PATH = r"C:\Users\LENOVO\Desktop\宝妈中转表.xlsx"

# ===================


# 获取tenant_access_token
def get_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    res = requests.post(url, json={
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }).json()
    return res["tenant_access_token"]


# 上传图片
def upload_image(token, file_path):
    url = "https://open.feishu.cn/open-apis/drive/v1/files/upload_all"
    headers = {
        "Authorization": f"Bearer {token}"
    }

    files = {
        'file': open(file_path, 'rb')
    }

    data = {
        "file_name": os.path.basename(file_path),
        "parent_type": "bitable_file",
        "parent_node": APP_TOKEN
    }

    res = requests.post(url, headers=headers, files=files, data=data).json()
    return res["data"]["file_token"]


# 写入多维表格
def insert_record(token, file_tokens):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    data = {
        "fields": {
            "个人码1": [{"file_token": file_tokens[0]}] if file_tokens[0] else [],
            "个人码2": [{"file_token": file_tokens[1]}] if file_tokens[1] else [],
            "个人码3": [{"file_token": file_tokens[2]}] if file_tokens[2] else [],
        }
    }

    requests.post(url, headers=headers, json=data)


# 提取Excel图片（关键）
def extract_images_by_cell():
    wb = load_workbook(EXCEL_PATH)
    ws = wb.active

    images = ws._images
    print(images)
    # 行 -> {列: 图片路径}
    row_map = {}

    for i, img in enumerate(images):
        row = img.anchor._from.row + 1
        col = img.anchor._from.col + 1  # 关键：列号
        if row == 1:
          continue
        img_path = f"temp_{i}.png"
        with open(img_path, "wb") as f:
            f.write(img._data())

        if row not in row_map:
            row_map[row] = {}

        row_map[row][col] = img_path

    return row_map


def main():
    token = get_token()
    row_map = extract_images_by_cell()
    for row, col_map in row_map.items():
        file_tokens = []
        for col in [1, 2, 3]:  # A B C列
            if col in col_map:
                token_img = upload_image(token, col_map[col])
                file_tokens.append(token_img)
            else:
                file_tokens.append(None)

        insert_record(token, file_tokens)
        print(f"第{row}行完成")


if __name__ == "__main__":
    main()
