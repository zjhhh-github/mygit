'''
校验报名录入表
需要报名录入表，教室老师场地表，内部通讯录表

DataFrame变量名：
enrollment_df （报名录入表）
teacher_campus_df （教务老师场地表）
internal_contact_df （内部通讯录表）

字典变量名：
student_recommendation_map （学员推荐映射）
valid_campus_list （有效校区列表）
valid_teacher_list （有效老师列表）

其他变量名：
target_month （目标月份）
valid_enrollment_items （有效报名项）
mailing_enrollment_items （需要邮寄报名项）
campus_based_enrollment_items （有校区报名项）
valid_order_amounts （有效订单金额）
enrollment_amount_map （报名项订单金额映射）
validation_errors （验证错误）
row_index （行索引）
'''
import pandas as pd
from data_validator import DataValidator

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)

enrollment_df = pd.read_excel(r"C:\Users\LENOVO\Desktop\报名录入.xlsx", sheet_name="报名录入", header=1)
teacher_campus_df = pd.read_excel(r"C:\Users\LENOVO\Desktop\报名录入.xlsx", sheet_name="教务老师场地", header=1)
internal_contact_df = pd.read_excel(r"C:\Users\LENOVO\Desktop\报名录入.xlsx", sheet_name="内部通讯录", header=1)

student_recommendation_map = {}
for row_index in internal_contact_df.index:
    if pd.notna(internal_contact_df.iloc[row_index]['学员']) and pd.notna(internal_contact_df.iloc[row_index]['推荐']):
        student_recommendation_map[internal_contact_df.iloc[row_index]['学员']] = internal_contact_df.iloc[row_index]['推荐']

valid_campus_list = set()
valid_teacher_list = set()
for row_index in teacher_campus_df.index:
    if pd.notna(teacher_campus_df.iloc[row_index, 1]):
        valid_campus_list.add(teacher_campus_df.iloc[row_index, 1])
    if pd.notna(teacher_campus_df.iloc[row_index, 8]):
        valid_teacher_list.add(teacher_campus_df.iloc[row_index, 8])

enrollment_df['交易时间'] = pd.to_datetime(enrollment_df['交易时间'], errors='coerce')
target_month = 12
valid_enrollment_items = ["陪跑","补外教","陪跑+线下","外教","陪跑+外教","外教转线下","补线下","线下","线下转外教"]
mailing_enrollment_items = ["陪跑","陪跑+外教","陪跑+线下"]
campus_based_enrollment_items = ["线下","补线下","外教转线下","陪跑+线下"]
valid_order_amounts = [1980,2180,5620,5900,6980,7600,7880]
enrollment_amount_map = {"陪跑":[1980,2180],"补外教":[5900],"陪跑+线下":[7600],"外教":[6980],"陪跑+外教":[7880],"补线下":[5900],"线下":[6980]}

validation_errors = {}

for row_index in enrollment_df.index:
    row_errors = []
    if len(str(enrollment_df.iloc[row_index]['交易订单编号'])) != 15:
        row_errors.append("交易编号有误")
    if pd.notna(enrollment_df.iloc[row_index]['交易时间']) and enrollment_df.iloc[row_index]['交易时间'].month != target_month:
        row_errors.append("交易时间有误")
    if enrollment_df.iloc[row_index]['报名项'] not in valid_enrollment_items:
        row_errors.append("报名项有误")
    if enrollment_df.iloc[row_index]['报名项'] in enrollment_amount_map:
        expected_amounts = enrollment_amount_map[enrollment_df.iloc[row_index]['报名项']]
        if expected_amounts == ['❌️']:
            if pd.notna(enrollment_df.iloc[row_index]['订单金额']):
                row_errors.append(f"报名项{enrollment_df.iloc[row_index]['报名项']}不应有订单金额")
        else:
            if enrollment_df.iloc[row_index]['订单金额'] not in expected_amounts:
                row_errors.append(f"订单金额有误，报名项{enrollment_df.iloc[row_index]['报名项']}应为{expected_amounts}之一")
    
    if len(enrollment_df.iloc[row_index]['孩子中文全名']) < 2:
        row_errors.append("孩子中文全名有误")
    if str(enrollment_df.iloc[row_index]['编号']) + "-" + str(enrollment_df.iloc[row_index]['孩子中文全名']) != enrollment_df.iloc[row_index]['编号+孩子中文全名']:
        row_errors.append("编号+孩子中文全名有误")
    if enrollment_df.iloc[row_index]['编号+孩子中文全名'] in student_recommendation_map:
        if pd.notna(enrollment_df.iloc[row_index]['推荐']) and enrollment_df.iloc[row_index]['推荐'] != student_recommendation_map[enrollment_df.iloc[row_index]['编号+孩子中文全名']]:
            row_errors.append(f"推荐信息有误，应为{student_recommendation_map[enrollment_df.iloc[row_index]['编号+孩子中文全名']]}")
    if enrollment_df.iloc[row_index]['报名项'] in mailing_enrollment_items:
        if enrollment_df.iloc[row_index]['新旧编号'] =="新":
            if enrollment_df.iloc[row_index]['孩子性别'] not in ["男","女"]:
                row_errors.append("孩子性别有误")
            if not DataValidator.is_china_mobile_precise(enrollment_df.iloc[row_index]['收件人电话']):
                row_errors.append("收件人电话有误")
            if enrollment_df.iloc[row_index]['发货'] == "✅":
                is_valid, reason = DataValidator.check_express_no_precise(enrollment_df.iloc[row_index]['单号'])
                if not is_valid:
                    row_errors.append(f"单号有误: {reason}")
    if enrollment_df.iloc[row_index]['报名项'] in campus_based_enrollment_items:
        if pd.notna(enrollment_df.iloc[row_index]['校区']):
            if enrollment_df.iloc[row_index]['校区'] not in valid_campus_list:
                row_errors.append(f"校区{enrollment_df.iloc[row_index]['校区']}有误")
        if pd.notna(enrollment_df.iloc[row_index]['老师']):
            if enrollment_df.iloc[row_index]['老师'] not in valid_teacher_list:
                row_errors.append(f"老师{enrollment_df.iloc[row_index]['老师']}有误")
    if row_errors:
        validation_errors[row_index+1082] = (row_errors, " ".join([str(x) for x in enrollment_df.iloc[row_index].values]))

for row_num in sorted(validation_errors.keys()):
    row_errors, data = validation_errors[row_num]
    print("=" * 80)
    print(f"第{row_num}行")
    print("-" * 80)
    print(data)
    print("-" * 80)
    for error_type in row_errors:
        print(error_type)
    print("=" * 80)
    print()
