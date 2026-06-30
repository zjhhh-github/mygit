import json
from pathlib import Path


json_path = Path(r'C:\Users\LENOVO\Downloads\意向学员数据_2026-04-27.json')
out_path = Path(r'C:\Users\LENOVO\Desktop\_脚本输出_1.txt')


def map_signup(v) -> str:
    if v == '已报名':
        return '已报名'
    if v == '未报名':
        return '未报名'
    return str(v) if v is not None else ''


def s(v) -> str:
    return str(v) if v is not None else ''


with json_path.open('r', encoding='utf-8') as f:
    data = json.load(f)

with out_path.open('w', encoding='utf-8') as f:
    f.write('\t'.join([
        '意向学员总微信号', "报名状态","来源状态","绑定状态","推荐人总微信号","绑定日期","解绑日期",
    ]) + '\n')
    for i in data:
        wx = s(i.get('意向学员微信号'))
        signup = map_signup(i.get('是否报名'))
        sources = i.get('来源') or []
        if len(sources) > 1:
            sources = [x for x in sources if s(x.get('来源微信号')) != '']
        if len(sources) > 1:
            bound = [x for x in sources if s(x.get('绑定状态')) == '有绑定']
            if bound:
                # 有绑定记录：取绑定日期最大的
                latest = max(bound, key=lambda x: s(x.get('绑定日期')))
                sources = [latest]
            else:
                # 无绑定记录：同样取绑定日期最大的作为唯一来源
                latest = max(sources, key=lambda x: s(x.get('绑定日期')))
                sources = [latest]
        if sources:
            for j in sources:
                f.write('\t'.join([
                    wx,
                    signup, '有来源',
                    s(j.get('绑定状态')),
                    s(j.get('来源微信号')),
                    s(j.get('绑定日期')),
                    s(j.get('解绑日期')),
                ]) + '\n')
        else:
            f.write('\t'.join([wx, signup, '无来源', '', '', '', '']) + '\n')
