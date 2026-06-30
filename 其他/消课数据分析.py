import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import re
import warnings

warnings.filterwarnings('ignore')


class ClassConsumptionAnalyzer:
    def __init__(self, base_path):
        self.base_path = Path(base_path)
        self.all_data = []
        self.summary_stats = {}

    def load_all_files(self):
        for region_dir in self.base_path.iterdir():
            if not region_dir.is_dir():
                continue

            region_name = region_dir.name
            print(f"\n处理地区: {region_name}")

            for file in region_dir.glob("*.xlsx"):
                if file.name.startswith("~$"):
                    continue

                try:
                    self._process_file(file, region_name)
                except Exception as e:
                    print(f"处理文件 {file.name} 时出错: {e}")

        print(f"\n总共处理了 {len(self.all_data)} 条数据记录")

    def _extract_month_from_filename(self, filename):
        """从文件名中提取月份信息"""
        # 文件名格式: export_25120_2026-05-25.xlsx.xlsx
        match = re.search(r'_(\d{4}-\d{2})-\d{2}', filename)
        if match:
            return match.group(1)
        return None

    def _process_file(self, file_path, region_name):
        try:
            xls = pd.ExcelFile(file_path)

            if '周出勤统计' not in xls.sheet_names:
                return

            df_attendance = pd.read_excel(xls, '周出勤统计')
            df_attendance['地区'] = region_name
            df_attendance['文件名'] = file_path.name
            
            # 从文件名提取月份
            month = self._extract_month_from_filename(file_path.name)
            df_attendance['统计月份'] = month

            if '统计日期区间说明' in xls.sheet_names:
                df_date_info = pd.read_excel(xls, '统计日期区间说明', header=None)
                date_info = df_date_info.iloc[0, 0] if not df_date_info.empty else ""
                df_attendance['统计区间'] = date_info

            self.all_data.append(df_attendance)

        except Exception as e:
            print(f"读取文件 {file_path} 失败: {e}")

    def analyze_data(self):
        if not self.all_data:
            print("没有可分析的数据")
            return

        df = pd.concat(self.all_data, ignore_index=True)

        df['课消金额'] = pd.to_numeric(df['课消金额'], errors='coerce').fillna(0)
        df['上课次数'] = pd.to_numeric(df['上课次数'], errors='coerce').fillna(0)
        df['到课次数'] = pd.to_numeric(df['到课次数'], errors='coerce').fillna(0)
        df['请假次数'] = pd.to_numeric(df['请假次数'], errors='coerce').fillna(0)
        df['旷课次数'] = pd.to_numeric(df['旷课次数'], errors='coerce').fillna(0)
        df['出勤率%'] = pd.to_numeric(df['出勤率%'], errors='coerce').fillna(0)
        df['实扣课时'] = pd.to_numeric(df['实扣课时'], errors='coerce').fillna(0)

        self.summary_stats = {
            '总记录数': len(df),
            '总课消金额': df['课消金额'].sum(),
            '平均课消金额': df['课消金额'].mean(),
            '总上课次数': df['上课次数'].sum(),
            '总到课次数': df['到课次数'].sum(),
            '总请假次数': df['请假次数'].sum(),
            '总旷课次数': df['旷课次数'].sum(),
            '平均出勤率': df['出勤率%'].mean(),
            '总实扣课时': df['实扣课时'].sum(),
        }

        self.summary_stats['地区统计'] = df.groupby('地区').agg({
            '课消金额': 'sum',
            '上课次数': 'sum',
            '到课次数': 'sum',
            '出勤率%': 'mean',
            '实扣课时': 'sum',
        }).to_dict('index')

        # 新增：分月份统计
        self.summary_stats['月份统计'] = df.groupby('统计月份').agg({
            '课消金额': 'sum',
            '上课次数': 'sum',
            '到课次数': 'sum',
            '出勤率%': 'mean',
            '实扣课时': 'sum',
            '请假次数': 'sum',
            '旷课次数': 'sum',
        }).sort_index().to_dict('index')

        # 新增：分地区+月份统计
        self.summary_stats['地区月份统计'] = df.groupby(['地区', '统计月份']).agg({
            '课消金额': 'sum',
            '上课次数': 'sum',
            '到课次数': 'sum',
            '出勤率%': 'mean',
            '实扣课时': 'sum',
        }).to_dict('index')

        self.summary_stats['学员排名'] = df.nlargest(20, '课消金额')[
            ['学员姓名', '地区', '课消金额', '上课次数', '到课次数', '出勤率%']
        ].to_dict('records')

        self.summary_stats['出勤率分析'] = {
            '高出勤率学员数': len(df[df['出勤率%'] >= 90]),
            '中等出勤率学员数': len(df[(df['出勤率%'] >= 70) & (df['出勤率%'] < 90)]),
            '低出勤率学员数': len(df[df['出勤率%'] < 70]),
        }

        self.summary_stats['请假旷课分析'] = {
            '有请假记录学员数': len(df[df['请假次数'] > 0]),
            '有旷课记录学员数': len(df[df['旷课次数'] > 0]),
            '全勤学员数': len(df[(df['请假次数'] == 0) & (df['旷课次数'] == 0) & (df['上课次数'] > 0)]),
        }

        return df

    def generate_report(self):
        print("\n" + "="*70)
        print("消课数据分析报告")
        print("="*70)

        print(f"\n【总体概况】")
        print(f"总记录数: {self.summary_stats.get('总记录数', 0)}")
        print(f"总课消金额: CNY {self.summary_stats.get('总课消金额', 0):,.2f}")
        print(f"平均课消金额: CNY {self.summary_stats.get('平均课消金额', 0):,.2f}")
        print(f"总上课次数: {self.summary_stats.get('总上课次数', 0)}")
        print(f"总到课次数: {self.summary_stats.get('总到课次数', 0)}")
        print(f"总请假次数: {self.summary_stats.get('总请假次数', 0)}")
        print(f"总旷课次数: {self.summary_stats.get('总旷课次数', 0)}")
        print(f"平均出勤率: {self.summary_stats.get('平均出勤率', 0):.1f}%")
        print(f"总实扣课时: {self.summary_stats.get('总实扣课时', 0):.1f}")

        print(f"\n【分月份统计】")
        print(f"{'月份':<12}{'课消金额':<15}{'上课次数':<10}{'到课次数':<10}{'出勤率%':<10}{'实扣课时':<10}{'请假次数':<10}{'旷课次数':<10}")
        print("-"*85)
        month_stats = self.summary_stats.get('月份统计', {})
        for month in sorted(month_stats.keys()):
            stats = month_stats[month]
            print(f"{month:<12}CNY {stats['课消金额']:>10,.2f}  {stats['上课次数']:<10}{stats['到课次数']:<10}{stats['出勤率%']:<10.1f}{stats['实扣课时']:<10.1f}{stats['请假次数']:<10}{stats['旷课次数']:<10}")

        print(f"\n【地区统计】")
        for region, stats in self.summary_stats.get('地区统计', {}).items():
            print(f"\n{region}:")
            print(f"  课消金额: CNY {stats['课消金额']:,.2f}")
            print(f"  上课次数: {stats['上课次数']}")
            print(f"  到课次数: {stats['到课次数']}")
            print(f"  平均出勤率: {stats['出勤率%']:.1f}%")
            print(f"  实扣课时: {stats['实扣课时']:.1f}")

        print(f"\n【分地区月份统计】")
        region_month_stats = self.summary_stats.get('地区月份统计', {})
        for (region, month), stats in sorted(region_month_stats.items()):
            print(f"{region} - {month}:")
            print(f"  课消金额: CNY {stats['课消金额']:,.2f}")
            print(f"  上课次数: {stats['上课次数']}")
            print(f"  到课次数: {stats['到课次数']}")
            print(f"  平均出勤率: {stats['出勤率%']:.1f}%")
            print(f"  实扣课时: {stats['实扣课时']:.1f}")

        print(f"\n【出勤率分析】")
        attendance_analysis = self.summary_stats.get('出勤率分析', {})
        print(f"高出勤率学员(≥90%): {attendance_analysis.get('高出勤率学员数', 0)}人")
        print(f"中等出勤率学员(70%-90%): {attendance_analysis.get('中等出勤率学员数', 0)}人")
        print(f"低出勤率学员(<70%): {attendance_analysis.get('低出勤率学员数', 0)}人")

        print(f"\n【请假旷课分析】")
        absence_analysis = self.summary_stats.get('请假旷课分析', {})
        print(f"有请假记录学员: {absence_analysis.get('有请假记录学员数', 0)}人")
        print(f"有旷课记录学员: {absence_analysis.get('有旷课记录学员数', 0)}人")
        print(f"全勤学员: {absence_analysis.get('全勤学员数', 0)}人")

        print(f"\n【课消金额TOP20学员】")
        print(f"{'排名':<6}{'学员姓名':<20}{'地区':<15}{'课消金额':<12}{'上课次数':<8}{'到课次数':<8}{'出勤率%':<8}")
        print("-"*80)
        for idx, student in enumerate(self.summary_stats.get('学员排名', []), 1):
            print(f"{idx:<6}{student['学员姓名']:<20}{student['地区']:<15}CNY {student['课消金额']:>8.2f}  {student['上课次数']:<8}{student['到课次数']:<8}{student['出勤率%']:<8.1f}")

        print("\n" + "="*70)
        print("分析完成")
        print("="*70)

    def export_to_excel(self, output_path):
        if not self.all_data:
            print("没有可导出的数据")
            return

        df = pd.concat(self.all_data, ignore_index=True)

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='原始数据', index=False)

            summary_df = pd.DataFrame([self.summary_stats])
            summary_df.to_excel(writer, sheet_name='总体统计', index=False)

            if '地区统计' in self.summary_stats:
                region_df = pd.DataFrame(self.summary_stats['地区统计']).T
                region_df.to_excel(writer, sheet_name='地区统计')

            # 新增：月份统计sheet
            if '月份统计' in self.summary_stats:
                month_df = pd.DataFrame(self.summary_stats['月份统计']).T
                month_df.to_excel(writer, sheet_name='月份统计')

            # 新增：地区月份统计sheet
            if '地区月份统计' in self.summary_stats:
                region_month_df = pd.DataFrame(self.summary_stats['地区月份统计']).T
                region_month_df.reset_index(inplace=True)
                region_month_df.columns = ['地区', '统计月份', '课消金额', '上课次数', '到课次数', '出勤率%', '实扣课时']
                region_month_df.to_excel(writer, sheet_name='地区月份统计', index=False)

            if '学员排名' in self.summary_stats:
                top_students_df = pd.DataFrame(self.summary_stats['学员排名'])
                top_students_df.to_excel(writer, sheet_name='学员排名', index=False)

        print(f"\n数据已导出到: {output_path}")


def main():
    base_path = r"D:\桌面文件\新建文件夹\原始数据"
    analyzer = ClassConsumptionAnalyzer(base_path)

    print("开始加载消课数据...")
    analyzer.load_all_files()

    print("\n开始分析数据...")
    df = analyzer.analyze_data()

    print("\n生成分析报告...")
    analyzer.generate_report()

    output_path = r"D:\桌面文件\新建文件夹\消课数据分析报告.xlsx"
    analyzer.export_to_excel(output_path)


if __name__ == "__main__":
    main()