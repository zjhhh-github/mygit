import sqlite3


# 数据源与输入输出路径（保持原路径不变，避免影响现有使用习惯）
DB_PATH = r'C:\Users\LENOVO\Desktop\contact.db'
INPUT_FILE_PATH = r'C:\Users\LENOVO\Desktop\_脚本输入_1.txt'
OUTPUT_FILE_PATH = r'D:\桌面文件\新建文件夹\数据结构处理\售前通讯录\微信号.txt'

# 是否打印详细日志；大量数据时逐行 print 会明显拖慢速度，默认关闭
VERBOSE = False


def query_contact(cursor, remark, cache):
    """
    查询单个 remark 对应的数据，并使用缓存避免重复查库。
    返回值为 (username, alias, output_name)；未找到时返回 None。
    """
    # 命中缓存时直接返回，减少重复 SQL 查询开销
    if remark in cache:
        return cache[remark]
    # if "-空" not in remark:
    #     return None
    # 只需要第一条记录，因此使用 fetchone() 代替 fetchall()，减少内存与 Python 层处理
    cursor.execute(
        'SELECT username, alias FROM contact WHERE remark like ? LIMIT 1',
        (f'%{remark.split('-')[0]}%',)
    )
    row = cursor.fetchone()

    if row:
        username, alias = row
        # 业务规则保持不变：alias 去空白后非空则优先，否则使用 username
        output_name = alias.strip() if alias and alias.strip() else username
        result = (username, alias, output_name)
    else:
        result = None

    # 无论是否命中都写入缓存，避免同一 remark 的重复查询
    cache[remark] = result
    return result


def main():
    # 使用 with 管理连接，确保异常情况下也能正确释放资源
    with sqlite3.connect(DB_PATH) as conn, \
            open(INPUT_FILE_PATH, 'r', encoding='utf-8') as input_file, \
            open(OUTPUT_FILE_PATH, 'w', encoding='utf-8') as out_file:
        cursor = conn.cursor()

        # remark -> (username, alias, output_name) 或 None
        cache = {}
        write_line = out_file.write

        for raw_line in input_file:
            remark = raw_line.strip()
            if not remark or '-空' in remark:
                write_line(f'{remark}\t\t\t\n')
                continue

            result = query_contact(cursor, remark, cache)
            if result:
                username, alias, output_name = result
                if VERBOSE:
                    print(remark)
                    print(output_name)
                # 输出格式保持原样：remark、username、alias、output_name
                write_line(f'{remark}\t{alias}\t{username}\t{output_name}\n')
            else:
                if VERBOSE:
                    print(remark)
                    print('未找到')
                # 未命中结果也落盘，保证输入与输出条目可对齐
                write_line(f'{remark}\t\t\t\n')


if __name__ == '__main__':
    main()
