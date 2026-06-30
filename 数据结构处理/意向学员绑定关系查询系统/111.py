# # 使用提醒:
# # 1. xbot包提供软件自动化、数据表格、Excel、日志、AI等功能
# # 2. package包提供访问当前应用数据的功能，如获取元素、访问全局变量、获取资源文件等功能
# # 3. 当此模块作为流程独立运行时执行main函数
# # 4. 可视化流程中可以通过"调用模块"的指令使用此模块

# import requests
# def main():
#     session = requests.Session()
#     session.trust_env = False   # 🚨 关键：不使用系统代理
#     app_id = "cli_a96f36ed1538dbcf"
#     app_secret = "0XiTHVpP9zbnXJWPSwM8DdxXpPwxlQRB"
#     image_path = r"C:\Users\LENOVO\Desktop\截屏.jpg"
#     receive_id = "43f8d3df"

#     # 获取token
#     token = requests.post(
#         "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
#         json={"app_id": app_id, "app_secret": app_secret}
#     ).json()["tenant_access_token"]

#     # 上传图片
#     headers = {"Authorization": f"Bearer {token}"}
#     files = {
#         "image_type": (None, "message"),
#         "image": open(image_path, "rb")
#     }

#     img_res = requests.post(
#         "https://open.feishu.cn/open-apis/im/v1/images",
#         headers=headers,
#         files=files
#     ).json()
#     print("上传返回：", img_res)
#     image_key = img_res["data"]["image_key"]

#     # 发消息
#     requests.post(
#         "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=user_id",
#         headers={
#             "Authorization": f"Bearer {token}",
#             "Content-Type": "application/json"
#         },
#         json={
#             "receive_id": receive_id,
#             "msg_type": "image",
#             "content": {"image_key": image_key}
#         }
#     )

#     print("发送成功")

# if __name__ == "__main__":
#   main()
l1 = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20]
l2 = [1,2,3,4,5,6,7,8,9,10,21]
print(set(l1)-set(l2))
