def find_chengyuan(name_list = ['倪佳毅','程中宥','杨昕林']):
    # 1. 导入内置的sqlite3库
    import sqlite3
    # 2. 连接SQLite3数据库
    # 说明：
    # - 若文件存在（如test.db），直接连接该本地数据库文件
    # - 若文件不存在，会在当前Python脚本目录下自动创建该数据库文件
    # - 若想创建内存数据库（关闭后数据丢失），可将路径改为 :memory:
    conn = sqlite3.connect('C:\\Users\\LENOVO\\Desktop\\contact.db')  # 本地文件数据库（推荐）
    # conn = sqlite3.connect(':memory:')  # 内存数据库（临时使用）

    # 3. 创建游标对象（用于执行SQL语句）
    cursor = conn.cursor()
    all_chengyuan = []
    for name in name_list:
        create_table_sql = f'''
        SELECT 
            DISTINCT remark,
            CASE 
                WHEN INSTR(remark, '-') > 0
                THEN SUBSTR(remark, 1, INSTR(remark, '-') - 1)
                ELSE remark
            END AS number
        FROM contact WHERE username in 
            (SELECT username FROM name2id WHERE rowid IN 
                (SELECT member_id FROM chatroom_member WHERE room_id in 
                    (SELECT room_id_ FROM chat_room_info_detail WHERE username_ in 
                        (SELECT username FROM contact WHERE remark like "{name} 孵化群%")
                    )
                )			
            )  ORDER BY number ASC;
        '''
        if name == "倪佳毅":    
            nijiayi_chengyuan = [i[1] for i in cursor.execute(create_table_sql).fetchall() if i[1] is not None and i[1][:3] == '¿¿¿']
        if name == "程中宥":
            chengzhongyou_chengyuan = [i[1] for i in cursor.execute(create_table_sql).fetchall() if i[1] is not None and i[1][:3] == '¿¿¿']
        if name == "杨昕林":
            yingxinlin_chengyuan = [i[1] for i in cursor.execute(create_table_sql).fetchall() if i[1] is not None and i[1][:3] == '¿¿¿']
        all_chengyuan = all_chengyuan + [i[1] for i in cursor.execute(create_table_sql).fetchall() if i[1] is not None and i[1][:3] == '¿¿¿']
        all_chengyuan = list(set(all_chengyuan))
    return nijiayi_chengyuan,chengzhongyou_chengyuan,yingxinlin_chengyuan,all_chengyuan

def neibutongxunlu(path = r"C:\Users\LENOVO\Desktop\_脚本输入_2.txt"):
    xueyuan2dailin = {}
    with open(path, 'r', encoding='utf-8') as f:
        f = f.readlines()
        for i in f:
            i = i.split('\t')
            xueyuan2dailin[i[0].split('-')[0]] = i[3]
    return xueyuan2dailin

def xueyuanxinxiluru(path = r"C:\Users\LENOVO\Desktop\_脚本输入_1.txt"):
    l1 = []
    l2 = []
    with open(path, 'r', encoding='utf-8') as f:
        f = f.readlines()
        for j in f:
            l1.append(j.split("\t")[7])
            l2.append(j.split("\t")[25])
    return zip(l1,l2)
            
def main():
    nijiayi_chengyuan,chengzhongyou_chengyuan,yingxinlin_chengyuan,all_chengyuan = find_chengyuan()
    xueyuan2dailin = neibutongxunlu()
    stu2fuhua = xueyuanxinxiluru()
    with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'w', encoding='utf-8') as f:
        pass
    for i,j in stu2fuhua:
        # print(i,j)
        if j == "⚠️" or j == "❌" or j is not None:
            if i in all_chengyuan:
                # 在官方孵化群中
                guanfangfuhuaren = []
                if i in nijiayi_chengyuan:
                    guanfangfuhuaren.append("¿¿¿000032-倪佳毅")
                elif i in chengzhongyou_chengyuan:
                    guanfangfuhuaren.append("¿¿¿000067-程中宥")
                elif i in yingxinlin_chengyuan:
                    guanfangfuhuaren.append("¿¿¿000115-杨昕林")
                if len(guanfangfuhuaren) == 1:
                    guanfangfuhuaren = ' '.join(guanfangfuhuaren)
                else:
                    guanfangfuhuaren = '⚠️'
                with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                    f.write(f"{guanfangfuhuaren}\n")
            else:
                # 不在官方孵化群中
                try:
                    dailc = xueyuan2dailin[i]
                except:
                    dailc = "⚠️"
                with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                    f.write(f"{dailc}\n")
        else:
            with open(r"C:\Users\LENOVO\Desktop\_输出结果_1.txt", 'a', encoding='utf-8') as f:
                f.write(f"{j}\n")
if __name__ == '__main__':
    main()
