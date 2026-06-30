import re

class DataValidator:
    @staticmethod
    def is_china_mobile_precise(phone_num):
        """
        判断是否为中国大陆手机号（精准版，覆盖全号段）
        :param phone_num: 待验证的手机号（字符串/数字）
        :return: True/False
        """
        phone_str = re.sub(r'[^\d]', '', str(phone_num))
        pattern = r"""
            ^1(
                3[0-9]|          # 130-139
                4[5-9]|          # 145-149
                5[0-35-9]|       # 150-153,155-159
                66|              # 166
                7[0-9]|          # 170-179
                8[0-9]|          # 180-189
                9[0-9]           # 190-199
            )\d{8}$              # 后8位数字
        """
        return bool(re.match(pattern, phone_str, re.VERBOSE))

    @staticmethod
    def check_express_no_precise(express_no):
        """
        精准校验快递单号（识别快递公司+验证合法性）
        :param express_no: 待验证快递单号
        :return: (是否合法, 所属快递公司/错误原因)
        """
        clean_no = re.sub(r'[^0-9A-Za-z]', '', str(express_no).strip()).upper()
        no_length = len(clean_no)
        
        express_rules = {
            "顺丰速运": r'^\d{12}$|^\d{18}$',
            "中通快递": r'^\d{12}$',
            "圆通快递": r'^(YT)?\d{10}$|^(YT)?\d{12}$|^(YT)?\d{13}$|^(YT)?\d{14}$',
            "申通快递": r'^\d{12}$',
            "韵达快递": r'^\d{13}$',
            "EMS": r'^[A-Z]{2}\d{9}[A-Z]{2}$',
            "京东物流": r'^\d{10}$|^\d{12}$|^JD[A-Z0-9]{8,10}$',
            "极兔快递": r'^(JT)?\d{13}$'
        }
        
        for company, pattern in express_rules.items():
            if re.match(pattern, clean_no):
                return True, company
        
        if not (10 <= no_length <= 18):
            return False, f"单号长度非法（需10-18位），当前长度：{no_length}"
        elif re.match(r'^[A-Za-z]+$', clean_no):
            return False, "单号仅含字母，无数字，非法"
        else:
            return False, "未匹配到主流快递公司规则，格式可能非法"
