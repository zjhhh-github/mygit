from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
import json
import os
import io
import uuid

import pandas as pd
from werkzeug.utils import secure_filename

app = Flask(__name__)

# 存储数据的文件路径
DATA_FILE = 'map_data.json'
# 项目根目录（用于拼接数据文件的绝对路径）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_data():
    """加载地图标记数据"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_data(data):
    """保存地图标记数据"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _next_marker_id(markers):
    """生成不会碰撞的标记ID：取当前最大ID+1"""
    if not markers:
        return 1
    return max(m.get('id', 0) for m in markers) + 1

# ==================== 静态数据（统一数据源，供多个接口复用） ====================

PROVINCES_DATA = [
    {'name': '北京市', 'code': '110000', 'population': '2188万', 'area': '1.64万平方公里'},
    {'name': '天津市', 'code': '120000', 'population': '1387万', 'area': '1.19万平方公里'},
    {'name': '河北省', 'code': '130000', 'population': '7461万', 'area': '18.88万平方公里'},
    {'name': '山西省', 'code': '140000', 'population': '3492万', 'area': '15.67万平方公里'},
    {'name': '内蒙古自治区', 'code': '150000', 'population': '2404万', 'area': '118.3万平方公里'},
    {'name': '辽宁省', 'code': '210000', 'population': '4259万', 'area': '14.86万平方公里'},
    {'name': '吉林省', 'code': '220000', 'population': '2407万', 'area': '18.74万平方公里'},
    {'name': '黑龙江省', 'code': '230000', 'population': '3125万', 'area': '47.3万平方公里'},
    {'name': '上海市', 'code': '310000', 'population': '2487万', 'area': '6340.5平方公里'},
    {'name': '江苏省', 'code': '320000', 'population': '8475万', 'area': '10.72万平方公里'},
    {'name': '浙江省', 'code': '330000', 'population': '6457万', 'area': '10.55万平方公里'},
    {'name': '安徽省', 'code': '340000', 'population': '6103万', 'area': '14.01万平方公里'},
    {'name': '福建省', 'code': '350000', 'population': '4154万', 'area': '12.4万平方公里'},
    {'name': '江西省', 'code': '360000', 'population': '4519万', 'area': '16.69万平方公里'},
    {'name': '山东省', 'code': '370000', 'population': '1.02亿', 'area': '15.71万平方公里'},
    {'name': '河南省', 'code': '410000', 'population': '9937万', 'area': '16.7万平方公里'},
    {'name': '湖北省', 'code': '420000', 'population': '5927万', 'area': '18.59万平方公里'},
    {'name': '湖南省', 'code': '430000', 'population': '6644万', 'area': '21.18万平方公里'},
    {'name': '广东省', 'code': '440000', 'population': '1.26亿', 'area': '17.97万平方公里'},
    {'name': '广西壮族自治区', 'code': '450000', 'population': '5013万', 'area': '23.67万平方公里'},
    {'name': '海南省', 'code': '460000', 'population': '1008万', 'area': '3.54万平方公里'},
    {'name': '重庆市', 'code': '500000', 'population': '3212万', 'area': '8.24万平方公里'},
    {'name': '四川省', 'code': '510000', 'population': '8372万', 'area': '48.6万平方公里'},
    {'name': '贵州省', 'code': '520000', 'population': '3856万', 'area': '17.62万平方公里'},
    {'name': '云南省', 'code': '530000', 'population': '4721万', 'area': '39.41万平方公里'},
    {'name': '西藏自治区', 'code': '540000', 'population': '364万', 'area': '122.84万平方公里'},
    {'name': '陕西省', 'code': '610000', 'population': '3953万', 'area': '20.58万平方公里'},
    {'name': '甘肃省', 'code': '620000', 'population': '2502万', 'area': '45.4万平方公里'},
    {'name': '青海省', 'code': '630000', 'population': '593万', 'area': '72.23万平方公里'},
    {'name': '宁夏回族自治区', 'code': '640000', 'population': '720万', 'area': '6.64万平方公里'},
    {'name': '新疆维吾尔自治区', 'code': '650000', 'population': '2585万', 'area': '166.49万平方公里'},
    {'name': '台湾省', 'code': '710000', 'population': '2341万', 'area': '3.6万平方公里'},
    {'name': '香港特别行政区', 'code': '810000', 'population': '748万', 'area': '0.11万平方公里'},
    {'name': '澳门特别行政区', 'code': '820000', 'population': '68万', 'area': '0.03万平方公里'},
]

# ==================== 路由定义 ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get_markers')
def get_markers():
    """获取所有标记数据"""
    markers = load_data()
    return jsonify(markers)

@app.route('/api/get_country_info/<country_name>')
def get_country_info(country_name):
    """获取国家信息"""
    # 模拟国家数据，实际应用中可以从数据库或API获取
    countries = {
        'China': {'population': '14亿', 'area': '960万平方公里', 'capital': '北京'},
        'United States of America': {'population': '3.3亿', 'area': '983万平方公里', 'capital': '华盛顿'},
        'India': {'population': '13.8亿', 'area': '297万平方公里', 'capital': '新德里'},
        'Brazil': {'population': '2.1亿', 'area': '851万平方公里', 'capital': '巴西利亚'},
        'Russia': {'population': '1.46亿', 'area': '1710万平方公里', 'capital': '莫斯科'}
    }
    
    info = countries.get(country_name, {'population': '未知', 'area': '未知', 'capital': '未知'})
    info['name'] = country_name
    return jsonify(info)

@app.route('/api/get_province_info/<province_name>')
def get_province_info(province_name):
    """获取省份信息 —— 直接从 PROVINCES_DATA 查询，避免数据重复"""
    for p in PROVINCES_DATA:
        if p['name'] == province_name:
            return jsonify(p)
    return jsonify({'name': province_name, 'population': '未知', 'area': '未知'})


def _load_geo_json(relative_path, description):
    """通用的地理数据文件加载函数，避免重复的 try/except 模式"""
    file_path = os.path.join(BASE_DIR, *relative_path.split('/'))
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({'error': f'{description}文件未找到: {file_path}'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/china_geo_data')
def get_china_geo_data():
    """获取中国省市边界TopoJSON数据"""
    return _load_geo_json('src/data/china.topo.json', '中国省份边界数据')


@app.route('/api/inner_mongolia_geo_data')
def get_inner_mongolia_geo_data():
    """获取内蒙古自治区下辖市、盟边界TopoJSON数据"""
    return _load_geo_json('src/data/inner_mongolia/cities.topo.json', '内蒙古市盟边界数据')


@app.route('/api/huhehaote_districts_geo_data')
def get_huhehaote_districts_geo_data():
    """获取呼和浩特市下辖区县边界TopoJSON数据"""
    return _load_geo_json('src/data/inner_mongolia/huhehaote/districts.topo.json', '呼和浩特区县边界数据')

@app.route('/api/provinces')
def get_all_provinces():
    """获取所有省份数据"""
    return jsonify(PROVINCES_DATA)

@app.route('/api/province/<province_name>')
def get_province_by_name(province_name):
    """根据省份名称获取省份数据"""
    for province in PROVINCES_DATA:
        if province['name'] == province_name:
            return jsonify(province)
    return jsonify({'error': '省份未找到'}), 404

@app.route('/api/get_inner_mongolia_data')
def get_inner_mongolia_data():
    """获取内蒙古自治区行政划分数据"""
    inner_mongolia_data = {
        'name': '内蒙古自治区',
        'code': '150000',
        'cities': [
            {
                'name': '呼和浩特市',
                'code': '150100',
                'population': '344.6万',
                'area': '1.72万平方公里',
                'county_level_cities': ['新城区', '回民区', '玉泉区', '赛罕区', '土默特左旗', '托克托县', '和林格尔县', '清水河县', '武川县']
            },
            {
                'name': '包头市',
                'code': '150200',
                'population': '270.9万',
                'area': '2.77万平方公里',
                'county_level_cities': ['东河区', '昆都仑区', '青山区', '石拐区', '白云鄂博矿区', '九原区', '土默特右旗', '固阳县', '达尔罕茂明安联合旗']
            },
            {
                'name': '乌海市',
                'code': '150300',
                'population': '55.66万',
                'area': '1754平方公里',
                'county_level_cities': ['海勃湾区', '海南区', '乌达区']
            },
            {
                'name': '赤峰市',
                'code': '150400',
                'population': '429.4万',
                'area': '9.0021万平方公里',
                'county_level_cities': ['红山区', '元宝山区', '松山区', '阿鲁科尔沁旗', '巴林左旗', '巴林右旗', '林西县', '克什克腾旗', '翁牛特旗', '喀喇沁旗', '宁城县', '敖汉旗']
            },
            {
                'name': '通辽市',
                'code': '150500',
                'population': '313.9万',
                'area': '5.9535万平方公里',
                'county_level_cities': ['科尔沁区', '科尔沁左翼中旗', '科尔沁左翼后旗', '开鲁县', '库伦旗', '奈曼旗', '扎鲁特旗', '霍林郭勒市']
            },
            {
                'name': '鄂尔多斯市',
                'code': '150600',
                'population': '215.4万',
                'area': '8.6752万平方公里',
                'county_level_cities': ['东胜区', '康巴什区', '达拉特旗', '准格尔旗', '鄂托克前旗', '鄂托克旗', '杭锦旗', '乌审旗', '伊金霍洛旗']
            },
            {
                'name': '呼伦贝尔市',
                'code': '150700',
                'population': '222.1万',
                'area': '25.3万平方公里',
                'county_level_cities': ['海拉尔区', '扎赉诺尔区', '阿荣旗', '莫力达瓦达斡尔族自治旗', '鄂伦春自治旗', '鄂温克族自治旗', '陈巴尔虎旗', '新巴尔虎左旗', '新巴尔虎右旗', '满洲里市', '牙克石市', '扎兰屯市', '额尔古纳市', '根河市']
            },
            {
                'name': '巴彦淖尔市',
                'code': '150800',
                'population': '153.8万',
                'area': '6.51万平方公里',
                'county_level_cities': ['临河区', '五原县', '磴口县', '乌拉特前旗', '乌拉特中旗', '乌拉特后旗', '杭锦后旗']
            },
            {
                'name': '乌兰察布市',
                'code': '150900',
                'population': '170.6万',
                'area': '5.53万平方公里',
                'county_level_cities': ['集宁区', '卓资县', '化德县', '商都县', '兴和县', '凉城县', '察哈尔右翼前旗', '察哈尔右翼中旗', '察哈尔右翼后旗', '四子王旗', '丰镇市']
            }
        ],
        'leagues': [
            {
                'name': '兴安盟',
                'code': '152200',
                'population': '168.2万',
                'area': '5.9806万平方公里',
                'county_level_cities': ['乌兰浩特市', '阿尔山市', '科尔沁右翼前旗', '科尔沁右翼中旗', '扎赉特旗', '突泉县']
            },
            {
                'name': '锡林郭勒盟',
                'code': '152500',
                'population': '116.4万',
                'area': '20.3万平方公里',
                'county_level_cities': ['二连浩特市', '锡林浩特市', '阿巴嘎旗', '苏尼特左旗', '苏尼特右旗', '东乌珠穆沁旗', '西乌珠穆沁旗', '太仆寺旗', '镶黄旗', '正镶白旗', '正蓝旗', '多伦县']
            },
            {
                'name': '阿拉善盟',
                'code': '152900',
                'population': '26.24万',
                'area': '27万平方公里',
                'county_level_cities': ['阿拉善左旗', '阿拉善右旗', '额济纳旗']
            }
        ]
    }
    return jsonify(inner_mongolia_data)

@app.route('/api/get_city_info/<city_name>')
def get_city_info(city_name):
    """获取城市信息"""
    # 模拟城市数据
    cities = {
        '北京市': {'population': '2188万', 'area': '1.64万平方公里'},
        '上海市': {'population': '2487万', 'area': '6340.5平方公里'},
        '广州市': {'population': '1868万', 'area': '7434.4平方公里'},
        '深圳市': {'population': '1756万', 'area': '1997.47平方公里'},
        '南京市': {'population': '931万', 'area': '6587.02平方公里'},
        '苏州市': {'population': '1275万', 'area': '8657.32平方公里'}
    }
    
    info = cities.get(city_name, {'population': '未知', 'area': '未知'})
    info['name'] = city_name
    return jsonify(info)

@app.route('/api/add_marker', methods=['POST'])
def add_marker():
    """添加标记"""
    data = request.json
    markers = load_data()
    
    new_marker = {
        'id': _next_marker_id(markers),
        'lat': data.get('lat'),
        'lng': data.get('lng'),
        'personName': data.get('personName', '新人员'),
        'address': data.get('address', ''),
        'region': data.get('region', 'Unknown')
    }
    
    markers.append(new_marker)
    save_data(markers)
    
    return jsonify({'success': True, 'marker': new_marker})

@app.route('/api/delete_marker/<int:marker_id>', methods=['DELETE'])
def delete_marker(marker_id):
    """删除标记"""
    markers = load_data()
    markers = [m for m in markers if m['id'] != marker_id]
    save_data(markers)
    
    return jsonify({'success': True})

@app.route('/api/update_marker/<int:marker_id>', methods=['PUT'])
def update_marker(marker_id):
    """更新标记"""
    data = request.json
    markers = load_data()
    
    updated_marker = None
    for marker in markers:
        if marker['id'] == marker_id:
            marker.update(data)
            updated_marker = marker
            break
    
    if updated_marker is None:
        return jsonify({'success': False, 'message': '标记未找到'}), 404
    
    save_data(markers)
    return jsonify({'success': True, 'marker': updated_marker})

# 配置上传文件夹
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xlsx', 'xls'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/download_template')
def download_template():
    """下载Excel模板文件"""
    template_data = {
        'name': ['张三', '李四', '王五'],
        'latitude': [39.9042, 31.2304, 23.1291],
        'longitude': [116.4074, 121.4737, 113.2644],
        'region': ['北京市', '上海市', '广州市']
    }
    
    df = pd.DataFrame(template_data)
    
    # 将DataFrame保存到内存中的Excel文件
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name='map_marker_template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/api/upload_excel', methods=['POST'])
def upload_excel():
    """上传Excel文件进行批量标注"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        
        try:
            # 读取Excel文件
            df = pd.read_excel(filepath)
            
            # 验证必需的列
            required_columns = ['name', 'latitude', 'longitude']
            if not all(col in df.columns for col in required_columns):
                return jsonify({'success': False, 'message': f'Excel文件必须包含以下列: {required_columns}'}), 400
            
            # 从现有数据加载标记
            markers = load_data()
            
            new_markers_count = 0
            for _, row in df.iterrows():
                new_marker = {
                    'id': _next_marker_id(markers),
                    'lat': float(row['latitude']),
                    'lng': float(row['longitude']),
                    'personName': str(row['name']) if 'name' in row else '新人员',
                    'address': f"{row['latitude']:.6f}, {row['longitude']:.6f}",
                    'region': str(row['region']) if 'region' in row else 'Unknown'
                }
                
                markers.append(new_marker)
                new_markers_count += 1
            
            # 保存更新后的数据
            save_data(markers)
            
            return jsonify({
                'success': True, 
                'message': f'成功导入{new_markers_count}个标记',
                'total_markers': len(markers)
            })
        except Exception as e:
            return jsonify({'success': False, 'message': f'处理Excel文件时出错: {str(e)}'}), 500
        finally:
            try:
                os.remove(filepath)
            except OSError:
                pass
    
    return jsonify({'success': False, 'message': '不支持的文件格式'}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)