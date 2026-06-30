import requests
import pymysql
import time
from datetime import datetime
import json
import base64 

# -------------------------- 本地配置（需修改！） --------------------------
# 1. WPS开放平台配置
WPS_APP_ID = "AK20260120CCQSPD"          # 从开放平台复制
WPS_APP_SECRET = "c0afda6aaa6afc59791a6540bd7f50a0"  # 从开放平台复制（显示需验证）
WPS_AUTH_URL = "https://openapi.wps.cn/oauth2/token"  # 令牌刷新地址

# 2. 本地MySQL配置（重点！适配本地数据库）
MYSQL_HOST = "192.168.1.88"    # 本地MySQL IP（本机用127.0.0.1，局域网用本机内网IP）
MYSQL_PORT = 13306           # 本地MySQL端口（默认3306）
MYSQL_USER = "root"         # 本地MySQL用户名
MYSQL_PASS = "MySql@123456"  # 本地MySQL密码
MYSQL_DB = "wps_sync"       # 要创建的数据库名

# -------------------------- 1. 自动获取/刷新WPS AccessToken --------------------------
def get_wps_token():
    """通过AppID+AppSecret获取AccessToken（自动刷新）"""
    # 使用标准 OAuth2 参数名
    data = {
        "grant_type": "client_credentials",
        "client_id": WPS_APP_ID,      # 使用 client_id 而不是 appid
        "client_secret": WPS_APP_SECRET,  # 使用 client_secret 而不是 secret
        "scope": "kso.file.readwrite"
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        print(f"正在请求令牌，ClientID: {WPS_APP_ID[:8]}..., Scope: kso.file.readwrite")
        resp = requests.post(WPS_AUTH_URL, data=data, headers=headers, timeout=10)
        print(f"HTTP状态码: {resp.status_code}")
        
        response_json = resp.json()
        print(f"响应内容: {response_json}")
        
        if resp.status_code == 200:
            access_token = response_json.get("access_token")
            expires_in = response_json.get("expires_in", 7200)
            if access_token:
                print(f"获取AccessToken成功，有效期{expires_in}秒")
                return access_token
            else:
                print(f"响应中未包含有效的access_token")
                return None
        else:
            print(f"请求失败，状态码: {resp.status_code}")
            return None
            
    except Exception as e:
        print(f"获取AccessToken失败：{e}")
        return None
# -------------------------- 2. 创建本地MySQL表（首次运行） --------------------------
def create_local_mysql_table():
    """在本地MySQL创建WPS文档同步表"""
    try:
        # 连接本地MySQL（注意charset=utf8mb4避免中文乱码）
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            charset="utf8mb4"
        )
        cursor = conn.cursor()

        # 创建数据库（本地MySQL若无此库则自动创建）
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB} DEFAULT CHARSET utf8mb4")
        cursor.execute(f"USE {MYSQL_DB}")

        # 创建同步表（存储文档核心信息）
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS wps_documents (
            doc_id VARCHAR(100) PRIMARY KEY COMMENT 'WPS文档唯一ID',
            doc_name VARCHAR(255) NOT NULL COMMENT '文档标题',
            doc_type VARCHAR(50) COMMENT '文档类型（docx/xlsx/pptx/txt）',
            doc_content LONGTEXT COMMENT '文档文本内容',
            doc_url VARCHAR(500) COMMENT '文档在线链接',
            create_time DATETIME COMMENT '文档创建时间',
            update_time DATETIME COMMENT '文档最后修改时间',
            creator VARCHAR(100) COMMENT '创建人',
            sync_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '同步到MySQL的时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='WPS云文档同步表';
        """
        cursor.execute(create_table_sql)
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ 本地MySQL表创建/检查完成")
    except pymysql.Error as e:
        print(f"❌ 本地MySQL连接/建表失败：{e}")
        exit(1)  # 建表失败直接退出

# -------------------------- 3. 获取WPS云文档列表 --------------------------
def get_wps_user_info(access_token):
    """获取用户信息以测试 API 连接"""
    url = "https://openapi.wps.cn/v1/user/info"  # 获取用户信息的端点
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        print(f"正在测试用户信息 API: {url}")
        resp = requests.get(url, headers=headers, timeout=10)
        print(f"用户信息API状态码: {resp.status_code}")
        if resp.status_code == 200:
            user_data = resp.json()
            print(f"用户信息: {user_data}")
            return True
        else:
            print(f"用户信息API失败: {resp.text}")
            return False
    except Exception as e:
        print(f"测试用户信息API时出错: {e}")
        return False

def get_wps_doc_list(access_token):
    """拉取WPS云文档列表（分页获取所有文档）"""
    # 首先测试用户信息API
    if not get_wps_user_info(access_token):
        print("❌ 用户信息API测试失败，可能认证有问题")
        return []
    
    doc_list = []
    page_num = 1
    page_size = 100  # 每页100条，可调整

    # 尝试带不同参数的 API 端点
    possible_urls = [
        "https://openapi.wps.cn/v1/cloud/docs",  # 云文档
        "https://openapi.wps.cn/v1/cloudspace",  # 云空间
        "https://openapi.wps.cn/v1/kso/drive/v1/files",  # KSO驱动v1文件
        "https://openapi.wps.cn/v1/drive/v2/files",  # 驱动v2文件
        "https://openapi.wps.cn/v1/storage/object",  # 存储对象
    ]

    for url in possible_urls:
        print(f"正在尝试 API 端点: {url}")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        # 尝试不同的参数结构
        params = {
            "page_num": page_num, 
            "page_size": page_size,
            "type": "all",  # 可能需要指定文档类型
            "order_by": "modified_time"  # 可能需要排序参数
        }

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            print(f"端点 {url} 返回状态码: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                print(f"成功获取数据，响应结构: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                
                # 尝试不同的数据结构
                current_docs = []
                if "data" in data and "items" in data["data"]:
                    current_docs = data["data"]["items"]
                elif "data" in data and "files" in data["data"]:
                    current_docs = data["data"]["files"]
                elif "data" in data and "docs" in data["data"]:
                    current_docs = data["data"]["docs"]
                elif "items" in data:
                    current_docs = data["items"]
                elif isinstance(data, list):
                    current_docs = data
                
                if current_docs:
                    doc_list.extend(current_docs)
                    print(f"✅ 成功获取到 {len(current_docs)} 个项目")
                    break
                else:
                    print(f"⚠️  {url} 返回了200状态码，但没有找到文档数据")
            else:
                print(f"⚠️  {url} 返回状态码: {resp.status_code}, 响应: {resp.text}")
                
        except Exception as e:
            print(f"❌ 尝试 {url} 时发生错误：{e}")
            continue
    
    if not doc_list:
        print("❌ 尝试了多个API端点都未能获取到文档列表")
        
    return doc_list

# -------------------------- 4. 获取单篇文档内容 --------------------------
def get_wps_doc_content(access_token, doc_id):
    """获取单篇WPS文档的文本内容（适配不同文档类型）"""
    url = f"https://openapi.wps.cn/v1/cloud/doc/content/{doc_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        content_data = resp.json().get("data", {})
        
        # 不同文档类型的内容提取逻辑（简化版，优先取文本）
        if content_data.get("type") == "text":
            return content_data.get("content", "")
        elif content_data.get("type") in ["docx", "wps"]:
            return content_data.get("text_content", "")  # 提取Word文本
        elif content_data.get("type") in ["xlsx", "et"]:
            return json.dumps(content_data.get("sheet_data", []), ensure_ascii=False)  # Excel转JSON
        else:
            return f"【暂不支持解析该类型文档：{content_data.get('type')}】"
    
    except Exception as e:
        print(f"❌ 文档{doc_id}内容提取失败：{e}")
        return ""

# -------------------------- 5. 同步到本地MySQL（增量更新） --------------------------
def sync_to_local_mysql(doc_list, access_token):
    """将WPS文档同步到本地MySQL（存在则更新，不存在则插入）"""
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database=MYSQL_DB,
            charset="utf8mb4"
        )
        cursor = conn.cursor()
        success_count = 0

        for doc in doc_list:
            # 提取文档核心信息
            doc_id = doc.get("doc_id")
            doc_name = doc.get("doc_name", "未知文档")
            doc_type = doc.get("doc_type", "")
            doc_url = doc.get("doc_url", "")
            creator = doc.get("creator", {}).get("name", "未知创建人")
            
            # 时间戳转换（WPS返回毫秒级，转成MySQL的DATETIME）
            create_time = datetime.fromtimestamp(doc.get("create_time")/1000) if doc.get("create_time") else None
            update_time = datetime.fromtimestamp(doc.get("update_time")/1000) if doc.get("update_time") else None
            
            # 获取文档内容
            doc_content = get_wps_doc_content(access_token, doc_id)

            # 增量同步SQL（主键冲突则更新）
            upsert_sql = """
            INSERT INTO wps_documents 
            (doc_id, doc_name, doc_type, doc_content, doc_url, create_time, update_time, creator)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            doc_name = VALUES(doc_name),
            doc_type = VALUES(doc_type),
            doc_content = VALUES(doc_content),
            doc_url = VALUES(doc_url),
            update_time = VALUES(update_time),
            creator = VALUES(creator),
            sync_time = CURRENT_TIMESTAMP;
            """

            try:
                cursor.execute(upsert_sql, (doc_id, doc_name, doc_type, doc_content, doc_url, create_time, update_time, creator))
                success_count += 1
                print(f"✅ 同步成功：{doc_name}")
            except Exception as e:
                print(f"❌ 同步失败：{doc_name} - {e}")

        conn.commit()
        cursor.close()
        conn.close()
        print(f"\n📊 同步完成！总计{len(doc_list)}篇文档，成功{success_count}篇")

    except pymysql.Error as e:
        print(f"❌ 本地MySQL写入失败：{e}")

# -------------------------- 主程序入口 --------------------------
if __name__ == "__main__":
    print("===== WPS云文档同步到本地MySQL =====")
    
    # 1. 先创建本地MySQL表（首次运行必执行）
    create_local_mysql_table()
    
    # 2. 获取有效的AccessToken
    access_token = get_wps_token()
    if not access_token:
        print("❌ 获取WPS令牌失败，同步终止")
        exit(1)
    
    # 3. 获取WPS云文档列表
    doc_list = get_wps_doc_list(access_token)
    if not doc_list:
        print("❌ 未获取到WPS云文档数据")
        exit(0)
    
    # 4. 同步到本地MySQL
    sync_to_local_mysql(doc_list, access_token)
    print("\n✅ 全部同步流程结束！")