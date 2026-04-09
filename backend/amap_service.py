# amap_service.py - 高德地图 Web 服务封装

try:
    # [本次修改-依赖兜底] requests 缺失时不要让整个后端导入失败。
    import requests
except ImportError:
    requests = None
from config import Config


class AMapService:
    """[高德接入-修改] 高德地图服务：封装地理编码和逆地理编码调用。"""

    def __init__(self):
        # [高德接入-修改] 从配置文件读取高德 Web 服务 API 的相关参数。
        self.api_key = Config.AMAP_WEB_SERVICE_KEY
        self.base_url = Config.AMAP_BASE_URL
        self.default_city = Config.AMAP_DEFAULT_CITY
        self.timeout = Config.AMAP_TIMEOUT

    def is_configured(self):
        """[高德接入-修改] 判断是否已经填写了高德 API Key。"""
        return bool(self.api_key and self.api_key.strip())

    def geocode(self, address, city=None):
        """[高德接入-修改] 地址转经纬度（地理编码）。"""
        # [本次修改-依赖兜底] 当前环境没装 requests 时，明确返回缺失依赖原因。
        if requests is None:
            return {"success": False, "message": "未安装 requests，暂时无法调用高德地理编码接口。"}

        if not self.is_configured():
            return {
                "success": False,
                "message": "高德地图 API Key 未配置，请先在 config.py 中填写 Config.AMAP_WEB_SERVICE_KEY。"
            }

        if not address or not str(address).strip():
            return {"success": False, "message": "address 不能为空"}

        params = {
            "key": self.api_key,
            "address": str(address).strip(),
            "output": "JSON",
        }

        selected_city = city if city is not None else self.default_city
        if selected_city:
            params["city"] = selected_city

        try:
            response = requests.get(
                f"{self.base_url}/geocode/geo",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "1":
                return {
                    "success": False,
                    "message": data.get("info", "高德地理编码调用失败"),
                    "raw": data,
                }

            geocodes = data.get("geocodes", [])
            if not geocodes:
                return {"success": False, "message": "未找到对应地址的坐标", "raw": data}

            first_result = geocodes[0]
            location = first_result.get("location", "")
            longitude, latitude = self._split_location(location)

            return {
                "success": True,
                "message": "地理编码成功",
                "address": first_result.get("formatted_address") or address,
                "province": first_result.get("province"),
                "city": first_result.get("city"),
                "district": first_result.get("district"),
                "adcode": first_result.get("adcode"),
                "location": location,
                "longitude": longitude,
                "latitude": latitude,
                "raw": data,
            }
        except requests.RequestException as e:
            return {"success": False, "message": f"请求高德地理编码接口失败：{e}"}
        except ValueError as e:
            return {"success": False, "message": f"解析高德地理编码结果失败：{e}"}

    def reverse_geocode(self, location=None, longitude=None, latitude=None, radius=1000, extensions="base"):
        """[高德接入-修改] 经纬度转地址（逆地理编码）。"""
        # [本次修改-依赖兜底] 当前环境没装 requests 时，明确返回缺失依赖原因。
        if requests is None:
            return {"success": False, "message": "未安装 requests，暂时无法调用高德逆地理编码接口。"}

        if not self.is_configured():
            return {
                "success": False,
                "message": "高德地图 API Key 未配置，请先在 config.py 中填写 Config.AMAP_WEB_SERVICE_KEY。"
            }

        if location:
            final_location = str(location).strip()
        elif longitude is not None and latitude is not None:
            final_location = f"{longitude},{latitude}"
        else:
            return {
                "success": False,
                "message": "请提供 location，或同时提供 longitude 和 latitude。"
            }

        params = {
            "key": self.api_key,
            "location": final_location,
            "radius": radius,
            "extensions": extensions,
            "output": "JSON",
        }

        try:
            response = requests.get(
                f"{self.base_url}/geocode/regeo",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "1":
                return {
                    "success": False,
                    "message": data.get("info", "高德逆地理编码调用失败"),
                    "raw": data,
                }

            regeocode = data.get("regeocode", {})
            address_component = regeocode.get("addressComponent", {})

            return {
                "success": True,
                "message": "逆地理编码成功",
                "location": final_location,
                "formatted_address": regeocode.get("formatted_address"),
                "country": address_component.get("country"),
                "province": address_component.get("province"),
                "city": address_component.get("city"),
                "district": address_component.get("district"),
                "township": address_component.get("township"),
                "street_number": address_component.get("streetNumber"),
                "raw": data,
            }
        except requests.RequestException as e:
            return {"success": False, "message": f"请求高德逆地理编码接口失败：{e}"}
        except ValueError as e:
            return {"success": False, "message": f"解析高德逆地理编码结果失败：{e}"}

    def _split_location(self, location):
        """[高德接入-修改] 将 '经度,纬度' 字符串拆分为两个浮点数。"""
        if not location or "," not in location:
            raise ValueError("location 字段格式不正确，应为 '经度,纬度'")

        longitude_text, latitude_text = location.split(",", 1)
        return float(longitude_text), float(latitude_text)


# [高德接入-修改] 创建全局高德地图服务实例，便于在 app.py 中直接导入使用。
amap_service = AMapService()
