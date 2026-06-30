#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试批量查询逻辑
"""

def test_batch_query_logic():
    """测试批量查询逻辑是否正确"""
    # 模拟数据库中的数据
    db_items = ['张三', '李四', '王五', '赵六', 'wxid123456', 'wxid789012', 'wxid345678', 'wxid901234']
    
    # 模拟从文本文件读取的查询项
    query_items = ['张三', '李四', '不存在的用户', 'wxid123456']
    
    print(f"数据库中的项目: {db_items}")
    print(f"查询项目: {query_items}")
    
    # 执行批量查询逻辑
    found_items = []
    not_found_items = []
    
    for item in query_items:
        if item in db_items:
            found_items.append(item)
        else:
            not_found_items.append(item)
    
    print(f"找到的项目: {found_items}")
    print(f"未找到的项目: {not_found_items}")
    
    # 验证结果
    assert len(found_items) == 3, f"应该找到3个项目，实际找到{len(found_items)}个"
    assert len(not_found_items) == 1, f"应该有1个未找到，实际{len(not_found_items)}个"
    assert '张三' in found_items, "张三应该在找到的列表中"
    assert '李四' in found_items, "李四应该在找到的列表中"
    assert 'wxid123456' in found_items, "wxid123456应该在找到的列表中"
    assert '不存在的用户' in not_found_items, "不存在的用户应该在未找到的列表中"
    
    print("\n批量查询逻辑测试通过！")
    
    # 模拟结果展示格式
    print(f"\n查询完成！共查询 {len(query_items)} 个项目")
    print(f"找到: {len(found_items)} 个")
    print(f"未找到: {len(not_found_items)} 个")
    
    print("\n【找到的项目】")
    for item in found_items:
        print(f"✓ '{item}' 在列表中")
    
    print("\n【未找到的项目】")
    for item in not_found_items:
        print(f"✗ '{item}' 不在列表中")

if __name__ == "__main__":
    test_batch_query_logic()