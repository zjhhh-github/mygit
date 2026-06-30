
name_list = []
with open(r"C:\Users\LENOVO\Desktop\_脚本输入_2.txt", "r", encoding="utf-8") as f:
    lines = f.readlines()
    for line in lines:
        line = line.strip().split('\t')
        name_list.append([line[0],line[1],line[2],line[3]])
num_name = []
name_list2 = []
with open(r"C:\Users\LENOVO\Desktop\_脚本输入_3.txt", "r", encoding="utf-8") as f:
    lines = f.readlines()
    for line in lines:
        line = line.strip().split('-')
        num_name.append((line[0],line[1]))
        name_list2.append(line[1])
list3 = []
for name in name_list:
    print(name,name_list2.count(name[0]))
    if name[0] not in name_list2 or name[2] != "⚠️":
        list3.append(name[2] + "\t" + name[3])
    else:
        if name_list2.count(name[0]) > 1:
            list3.append("⚠️\t⚠️")
            # print("⚠️")
        else:
            for item,item2 in num_name:
                if name[0] == item2:
                    list3.append(item+"\t"+item+"-"+name[0])
                    # print(item,name)
                    continue
with open(r"C:\Users\LENOVO\Desktop\_脚本输出_3.txt", "w", encoding="utf-8") as f:
    for line in list3:
        f.write(line + '\n')
