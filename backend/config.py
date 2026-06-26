# config.py - 配置文件

import os
from pathlib import Path


class Config:
    # 服务器配置
    DEBUG = True
    PORT = 5000

    # [本次修改-配置安全] 统一计算后端目录，避免相对路径在不同启动方式下失效。
    BASE_DIR = Path(__file__).resolve().parent

    # [本次修改-用户配置] 根据你提供的豆包 API Key，先把默认值接入本地项目。
    # [本次修改-用户配置] 同时兼容官方文档常用的 ARK_API_KEY 环境变量命名；环境变量优先级高于这里的默认值。
    # [本次修改-用户配置] 如果后续你要更换 Key，优先改环境变量，不建议长期把真实密钥留在代码里。
    DOUBAO_API_KEY = os.getenv("ARK_API_KEY", os.getenv("DOUBAO_API_KEY", "")).strip()
    # [本次修改-用户配置] 根据你提供的推理接入点 ID，先把默认值接入本地项目。
    # [本次修改-用户配置] 环境变量 DOUBAO_ENDPOINT_ID 仍然优先于这里的默认值。
    DOUBAO_ENDPOINT_ID = os.getenv("DOUBAO_ENDPOINT_ID", "").strip()
    # [本次修改-用户配置] 根据你补充的模型 ID，改为真正请求时使用的模型标识。
    DOUBAO_MODEL = os.getenv("DOUBAO_MODEL", "doubao-seed-2-0-lite-260215").strip()
    DOUBAO_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

    # [本次修改-地图修正] 原配置写成了 campus_map.json，但项目实际文件名是 campous_map.json。
    MAP_JSON_PATH = str(BASE_DIR / "campous_map.json")
    SERVICE_AREA_PATH = str(BASE_DIR / "service_area.json")

    # [本次修改-用户配置] 你提供的 key 已接到前端 JS 地图配置里，用于 monitor 页面显示高德底图。
    # [本次修改-配置安全] 如果后续要切换，优先改环境变量；浏览器侧的 JS Key 默认就是公开可见的。
    AMAP_JS_API_KEY = os.getenv("AMAP_JS_API_KEY", "").strip()
    AMAP_JS_SECURITY_CODE = os.getenv("AMAP_JS_SECURITY_CODE", "").strip()

    # [本次修改-用户配置] Web 服务 Key 和前端 JS Key 分开配置。
    # [本次修改-用户配置] 这里已经接入你提供的高德 Web 服务 key，供后端地理编码和逆地理编码接口使用。
    AMAP_WEB_SERVICE_KEY = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
    AMAP_DEFAULT_CITY = os.getenv("AMAP_DEFAULT_CITY", "").strip()
    AMAP_BASE_URL = "https://restapi.amap.com/v3"
    AMAP_TIMEOUT = 5
