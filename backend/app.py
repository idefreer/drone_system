from flask import Flask, request, jsonify, send_from_directory
try:
    from flask_cors import CORS
except ImportError:
    def CORS(app):
        return app
from datetime import datetime
import sqlite3
import os

# ===== 导入配置和服务 =====
import env_loader  # noqa: F401
from config import Config
from llm_service import llm_service
from map_service import map_service
from amap_service import amap_service

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))

app = Flask(__name__)
CORS(app)


@app.after_request
def add_api_cors_headers(response):
    if request.path.startswith('/api/'):
        origin = request.headers.get('Origin')
        allow_origin = '*' if not origin or origin == 'null' else origin
        response.headers['Access-Control-Allow-Origin'] = allow_origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = request.headers.get(
            'Access-Control-Request-Headers',
            'Content-Type, Authorization'
        )
        response.headers['Access-Control-Max-Age'] = '86400'
        response.headers['Vary'] = 'Origin'
    return response

# ===== 数据库配置 =====
# [本次修改-路径修正] 把数据库路径固定到 backend 目录下，避免从别的目录启动时找不到文件。
DATABASE = os.path.join(os.path.dirname(__file__), 'orders.db')

def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """初始化数据库：创建表"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medicine TEXT NOT NULL,
            location TEXT NOT NULL,
            priority TEXT NOT NULL,
            notes TEXT,
            status TEXT DEFAULT '等待中',
            create_time TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
    # [本次修改-终端兼容] 避免 Windows GBK 终端因 emoji 输出报错。
    print("[APP] 数据库初始化完成")


# [本次修改-后端整理] 抽出统一查询函数，避免不同接口重复写相同 SQL。
def fetch_orders(status=None):
    """按优先级获取订单；可选按状态筛选。"""
    conn = get_db()
    cursor = conn.cursor()
    if status:
        cursor.execute('''
            SELECT * FROM orders
            WHERE status = ?
            ORDER BY
                CASE priority
                    WHEN '高' THEN 1
                    WHEN '中' THEN 2
                    WHEN '低' THEN 3
                END,
                id ASC
        ''', (status,))
    else:
        cursor.execute('''
            SELECT * FROM orders
            ORDER BY
                CASE priority
                    WHEN '高' THEN 1
                    WHEN '中' THEN 2
                    WHEN '低' THEN 3
                END,
                id ASC
        ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def resolve_location_context(location):
    """解析订单地点，优先使用演示地图点位，其次使用高德经纬度。"""
    point = map_service.get_point_by_name(location)
    if point:
        return {
            'success': True,
            'source': 'map',
            'x': point.get('x'),
            'y': point.get('y'),
            'point': point,
            'geocode_result': None
        }

    if location and amap_service.is_configured():
        geocode_result = amap_service.geocode(location)
        if geocode_result.get('success'):
            return {
                'success': True,
                'source': 'amap',
                'longitude': geocode_result.get('longitude'),
                'latitude': geocode_result.get('latitude'),
                'geocode_result': geocode_result
            }
        return {
            'success': False,
            'source': 'amap',
            'message': geocode_result.get('message') or '地址解析失败',
            'geocode_result': geocode_result
        }

    return {
        'success': False,
        'source': 'unknown',
        'message': '未找到对应地图点位，且高德地图未配置'
    }


def is_location_within_service_area(location_context):
    """根据解析结果判断地点是否在配送范围内。"""
    if not map_service.has_service_area():
        return True

    if not location_context or not location_context.get('success'):
        return False

    if location_context.get('source') == 'map':
        point = location_context.get('point')
        return bool(point and map_service.is_point_in_service_area(point))

    if location_context.get('source') == 'amap':
        longitude = location_context.get('longitude')
        latitude = location_context.get('latitude')
        if longitude is None or latitude is None:
            return False
        return map_service.is_geocode_in_service_area(longitude, latitude)

    return False


def build_service_area_response():
    """统一组织配送范围返回数据。"""
    allowed_points = map_service.get_available_points()
    return {
        'active': map_service.has_service_area(),
        'service_area': map_service.get_service_area(),
        'allowed_locations': [point['name'] for point in allowed_points if point.get('type') != 'depot'],
        'all_locations': [point['name'] for point in map_service.get_points() if point.get('type') != 'depot']
    }

# 启动时初始化数据库
init_db()

# ===== 前端页面 =====
@app.route('/')
def index_page():
    """返回前端首页。"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:filename>')
def frontend_page(filename):
    """托管前端静态页面，避免直接 file:// 打开造成接口请求失败。"""
    if filename.startswith('api/'):
        return jsonify({'success': False, 'message': '接口不存在'}), 404
    return send_from_directory(FRONTEND_DIR, filename)


# ===== 健康检查 =====
@app.route('/api')
def home():
    return jsonify({
        'message': '无人机配送系统后端运行成功！',
        'status': 'ok',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

# ===== 订单接口 =====
@app.route('/api/orders', methods=['POST'])
def create_order():
    """接收新订单"""
    data = request.json
    
    if not data:
        return jsonify({'success': False, 'message': '没有接收到数据'}), 400
    
    medicine = data.get('medicine', '')
    location = data.get('location', '')
    priority = data.get('priority', '低')
    notes = data.get('notes', '')
    create_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not medicine or not location:
        return jsonify({'success': False, 'message': '请填写完整的药品和配送地点信息'}), 400

    location_context = resolve_location_context(location)
    if map_service.has_service_area() and not is_location_within_service_area(location_context):
        return jsonify({
            'success': False,
            'message': f'当前配送范围未覆盖“{location}”，请先到实时监控页重新圈定范围'
        }), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (medicine, location, priority, notes, create_time)
        VALUES (?, ?, ?, ?, ?)
    ''', (medicine, location, priority, notes, create_time))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()

    # [高德接入-修改] 创建订单后，尝试把用户填写的地址转成高德经纬度。
    # [高德接入-修改] 这里不影响订单主流程：就算高德没配置或请求失败，订单也照常创建成功。
    geocode_result = location_context.get('geocode_result') if location_context else None
    location_geocode = None
    if geocode_result and geocode_result.get('success'):
        location_geocode = {
            'address': geocode_result.get('address'),
            'location': geocode_result.get('location'),
            'longitude': geocode_result.get('longitude'),
            'latitude': geocode_result.get('latitude')
        }
    
    return jsonify({
        'success': True,
        'message': '订单创建成功',
        'order': {
            'id': new_id,
            'medicine': medicine,
            'location': location,
            'priority': priority,
            'notes': notes,
            'status': '等待中',
            'create_time': create_time
        },
        # [高德接入-修改] 如果高德成功解析地址，这里会把经纬度一起返回给前端。
        'location_geocode': location_geocode,
        'service_area': build_service_area_response()
    })

@app.route('/api/orders', methods=['GET'])
def get_orders():
    """获取所有订单（按优先级排序）"""
    # [本次修改-后端整理] 复用统一查询函数。
    orders = fetch_orders()
    return jsonify({'success': True, 'orders': orders})

@app.route('/api/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    """获取单个订单"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return jsonify({'success': True, 'order': dict(row)})
    else:
        return jsonify({'success': False, 'message': '订单不存在'}), 404

@app.route('/api/orders/<int:order_id>', methods=['DELETE'])
def delete_order(order_id):
    """删除订单"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM orders WHERE id = ?', (order_id,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    
    if affected > 0:
        return jsonify({'success': True, 'message': '订单已删除'})
    else:
        return jsonify({'success': False, 'message': '订单不存在'}), 404

@app.route('/api/orders/<int:order_id>/status', methods=['PUT'])
def update_order_status(order_id):
    """更新订单状态"""
    data = request.json
    new_status = data.get('status', '')
    
    if not new_status:
        return jsonify({'success': False, 'message': '请提供状态'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = ? WHERE id = ?', (new_status, order_id))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    
    if affected > 0:
        return jsonify({'success': True, 'message': f'订单状态已更新为{new_status}'})
    else:
        return jsonify({'success': False, 'message': '订单不存在'}), 404

# ===== 大模型规划路线接口 =====
@app.route('/api/plan-route', methods=['POST'])
def plan_route():
    """规划无人机配送路线"""
    # [本次修改-自然语言规划] 支持前端把自然语言调度要求一起传进来。
    request_data = request.get_json(silent=True) or {}
    natural_language_input = (request_data.get('natural_language_input') or '').strip()

    # [本次修改-后端整理] 统一获取待配送订单。
    tasks = fetch_orders(status='等待中')
    service_area_meta = build_service_area_response()

    if map_service.has_service_area():
        allowed_locations = set(service_area_meta['allowed_locations'])
        filtered_tasks = [task for task in tasks if task.get('location') in allowed_locations]
        excluded_count = len(tasks) - len(filtered_tasks)
        tasks = filtered_tasks
    else:
        excluded_count = 0
    
    if not tasks:
        message = '没有待配送的任务'
        if map_service.has_service_area() and excluded_count > 0:
            message = '当前等待中的订单都不在已圈定的配送范围内'
        return jsonify({
            'success': False,
            'message': message
        }), 400
    
    # 获取地图数据
    map_points = map_service.get_available_points()
    
    if not map_points:
        return jsonify({
            'success': False,
            'message': '地图数据未加载'
        }), 500
    
    # 调用大模型规划路线
    try:
        # [本次修改-自然语言规划] 把自然语言要求一起交给路径规划服务。
        route = llm_service.plan_route(tasks, map_points, natural_language_input=natural_language_input)
        route_note = route.get('note', '')
        if excluded_count > 0:
            extra_note = f'有 {excluded_count} 个等待中订单不在当前配送范围内，已自动忽略。'
            route['note'] = f'{route_note} {extra_note}'.strip()
        route['service_area'] = {
            **service_area_meta,
            'excluded_waiting_orders': excluded_count
        }
        return jsonify({
            'success': True,
            'route': route,
            'natural_language_input': natural_language_input
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'规划失败: {str(e)}'
        }), 500

# ===== 高德地图接口 =====
@app.route('/api/amap/geocode', methods=['POST'])
def amap_geocode():
    """[高德接入-修改] 地址转经纬度接口。"""
    data = request.json or {}
    address = data.get('address', '')
    city = data.get('city')

    if not address:
        return jsonify({'success': False, 'message': '请提供 address'}), 400

    result = amap_service.geocode(address, city)
    if result.get('success'):
        return jsonify(result)

    status_code = 503 if not amap_service.is_configured() else 400
    return jsonify(result), status_code


@app.route('/api/amap/config', methods=['GET'])
def amap_config():
    """返回前端高德地图所需的公开配置。"""
    return jsonify({
        'success': True,
        'js_api_key': Config.AMAP_JS_API_KEY,
        'security_js_code': Config.AMAP_JS_SECURITY_CODE,
        'js_api_configured': bool(Config.AMAP_JS_API_KEY),
        'web_service_configured': amap_service.is_configured()
    })


@app.route('/api/amap/regeo', methods=['POST'])
def amap_reverse_geocode():
    """[高德接入-修改] 经纬度转地址接口。"""
    data = request.json or {}
    location = data.get('location')
    longitude = data.get('longitude')
    latitude = data.get('latitude')
    radius = data.get('radius', 1000)
    extensions = data.get('extensions', 'base')

    if not location and (longitude is None or latitude is None):
        return jsonify({
            'success': False,
            'message': '请提供 location，或同时提供 longitude 和 latitude'
        }), 400

    result = amap_service.reverse_geocode(
        location=location,
        longitude=longitude,
        latitude=latitude,
        radius=radius,
        extensions=extensions
    )
    if result.get('success'):
        return jsonify(result)

    status_code = 503 if not amap_service.is_configured() else 400
    return jsonify(result), status_code


@app.route('/api/service-area', methods=['GET'])
def get_service_area():
    """获取当前配送范围。"""
    return jsonify({
        'success': True,
        **build_service_area_response()
    })


@app.route('/api/service-area', methods=['POST'])
def save_service_area():
    """保存配送范围。"""
    data = request.get_json(silent=True) or {}
    points = data.get('points', [])

    try:
        service_area = map_service.save_service_area(points)
    except Exception as exc:
        return jsonify({
            'success': False,
            'message': f'保存配送范围失败: {exc}'
        }), 400

    return jsonify({
        'success': True,
        'message': '配送范围已保存',
        'service_area': service_area,
        **build_service_area_response()
    })


@app.route('/api/service-area', methods=['DELETE'])
def delete_service_area():
    """清空配送范围。"""
    map_service.clear_service_area()
    return jsonify({
        'success': True,
        'message': '配送范围已清空',
        **build_service_area_response()
    })


@app.route('/api/campus-map', methods=['GET'])
def get_campus_map():
    """获取校园点位、建筑物和飞行连线配置。"""
    return jsonify({
        'success': True,
        'campus_map': map_service.get_campus_map()
    })


@app.route('/api/campus-map', methods=['POST'])
def save_campus_map():
    """保存校园点位、建筑物和飞行连线配置。"""
    data = request.get_json(silent=True) or {}
    campus_map = data.get('campus_map', data)

    try:
        saved_map = map_service.save_campus_map(campus_map)
    except Exception as exc:
        return jsonify({
            'success': False,
            'message': f'保存校园地图失败: {exc}'
        }), 400

    return jsonify({
        'success': True,
        'message': '校园地图已保存',
        'campus_map': saved_map,
        **build_service_area_response()
    })

# ===== 状态查看接口 =====
@app.route('/api/status', methods=['GET'])
def get_status():
    """查看各服务状态"""
    # [本次修改-状态修正] 直接统计订单数量，避免再去调用视图函数对象。
    total_orders = len(fetch_orders())
    return jsonify({
        'success': True,
        'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'services': {
            '大模型服务': {
                # [本次修改-状态修正] 按真实配置状态判断，而不是和示例字符串比较。
                'status': '已配置' if llm_service.is_configured() else '等待配置',
                'api_url': Config.DOUBAO_API_URL,
                'endpoint_id': Config.DOUBAO_ENDPOINT_ID or '未配置',
                # [本次修改-用户配置] 方便确认当前实际走的是公共模型名还是专属接入点。
                'model': Config.DOUBAO_MODEL or '未配置'
            },
            '地图服务': {
                'status': '已加载' if map_service.points else '等待加载',
                'points_count': len(map_service.points),
                'map_file': Config.MAP_JSON_PATH
            },
            '配送范围': {
                'status': '已启用' if map_service.has_service_area() else '未限制',
                'points_count': len((map_service.get_service_area() or {}).get('points', [])),
                'config_file': Config.SERVICE_AREA_PATH
            },
            '高德地图服务': {
                # [本次修改-高德状态] 前端 JS 地图和后端 Web 服务分开显示，避免误判“地图可显示但接口不可调”。
                'status': '部分已配置' if Config.AMAP_JS_API_KEY and not amap_service.is_configured() else ('已配置' if Config.AMAP_JS_API_KEY and amap_service.is_configured() else '等待配置'),
                'js_api_status': '已配置' if Config.AMAP_JS_API_KEY else '等待配置',
                'web_service_status': '已配置' if amap_service.is_configured() else '等待配置',
                'js_api_loader': 'https://webapi.amap.com/maps?v=2.0&key=YOUR_KEY',
                'geocode_api': f'{Config.AMAP_BASE_URL}/geocode/geo',
                'reverse_geocode_api': f'{Config.AMAP_BASE_URL}/geocode/regeo'
            }
        },
        'stats': {
            '总订单数': total_orders
        }
    })

# ===== 启动服务器 =====
if __name__ == '__main__':
    print("=" * 50)
    print("[APP] 无人机配送系统后端启动中...")
    print(f"[APP] 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    print("\n[APP] 当前状态：")
    print("   [OK] 基础API运行正常")
    # [本次修改-状态修正] 启动日志改为基于真实配置状态输出。
    if llm_service.is_configured():
        print("   [OK] 大模型服务：已配置")
    else:
        print("   [WAIT] 大模型服务：等待配置（请设置 DOUBAO_API_KEY 和 DOUBAO_ENDPOINT_ID 环境变量）")
    if map_service.points:
        print(f"   [OK] 校区地图：已加载（{len(map_service.points)}个点）")
    else:
        print("   [WAIT] 校区地图：等待文件")
    if Config.AMAP_JS_API_KEY:
        print("   [OK] 高德前端地图：已配置")
    else:
        print("   [WAIT] 高德前端地图：等待填写 AMAP_JS_API_KEY")
    # [高德接入-修改] 启动时输出高德服务配置状态，方便调试。
    if amap_service.is_configured():
        print("   [OK] 高德 Web 服务：已配置")
    else:
        print("   [WAIT] 高德 Web 服务：等待填写 API Key（环境变量 AMAP_WEB_SERVICE_KEY）")
    print("\n[APP] 前端首页: http://127.0.0.1:5000/")
    print("[APP] 实时监控: http://127.0.0.1:5000/monitor.html")
    print("[APP] 发起配送: http://127.0.0.1:5000/order.html")
    print("[APP] 健康检查: http://127.0.0.1:5000/api")
    print("[APP] 状态查看: http://127.0.0.1:5000/api/status")
    print("=" * 50)
    
    app.run(debug=True, port=5000)
