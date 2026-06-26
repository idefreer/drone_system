# map_service.py - 地图服务

import json
import os
from datetime import datetime

import env_loader  # noqa: F401
from config import Config


class MapService:
    """读取地图点位，并管理配送范围（电子围栏）。"""

    def __init__(self):
        self.map_file = Config.MAP_JSON_PATH
        self.service_area_file = Config.SERVICE_AREA_PATH
        self.map_data = None
        self.points = []
        self.service_area = None
        self.load_map()
        self.load_service_area()

    def load_map(self):
        """加载地图 JSON 文件；如果失败，则自动切换到演示地图。"""
        if os.path.exists(self.map_file):
            try:
                with open(self.map_file, "r", encoding="utf-8") as file:
                    self.map_data = json.load(file)

                if not isinstance(self.map_data, dict):
                    raise ValueError("地图 JSON 的顶层结构必须是对象(dict)")

                loaded_points = self.map_data.get("points", [])
                if not isinstance(loaded_points, list):
                    raise ValueError("地图 JSON 中的 points 字段必须是列表(list)")

                self.points = loaded_points
                print(f"[MAP] 地图加载成功：{len(self.points)} 个点")
                return
            except Exception as exc:
                print(f"[MAP] 地图加载失败：{exc}")

        self._create_demo_map()

    def load_service_area(self):
        """加载配送范围；未配置时返回空。"""
        if not os.path.exists(self.service_area_file):
            self.service_area = None
            return

        try:
            with open(self.service_area_file, "r", encoding="utf-8") as file:
                data = json.load(file)

            if not isinstance(data, dict):
                raise ValueError("配送范围数据必须是对象")

            if data.get("disabled") or not data.get("points"):
                self.service_area = None
                print("[MAP] 配送范围已关闭")
                return

            self._validate_service_area_payload(data)
            self.service_area = data
            print(f"[MAP] 已加载配送范围：{len(data['points'])} 个顶点")
        except Exception as exc:
            print(f"[MAP] 配送范围加载失败：{exc}")
            self.service_area = None

    def save_service_area(self, points):
        """保存配送范围多边形。"""
        payload = {
            "points": [self._normalize_vertex(point) for point in points],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._validate_service_area_payload(payload)

        with open(self.service_area_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        self.service_area = payload
        return payload

    def clear_service_area(self):
        """清空配送范围。"""
        self.service_area = None
        if os.path.exists(self.service_area_file):
            try:
                os.remove(self.service_area_file)
            except PermissionError:
                placeholder = {
                    "disabled": True,
                    "points": [],
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                with open(self.service_area_file, "w", encoding="utf-8") as file:
                    json.dump(placeholder, file, ensure_ascii=False, indent=2)

    def get_service_area(self):
        """返回当前配送范围。"""
        return self.service_area

    def has_service_area(self):
        """当前是否启用了配送范围。"""
        return bool(self.service_area and self.service_area.get("points"))

    def get_points(self):
        """返回所有点位。"""
        return self.points

    def get_campus_map(self):
        """返回完整校园地图配置。"""
        if not isinstance(self.map_data, dict):
            self.map_data = self._empty_campus_map()

        self.map_data.setdefault("name", "校园配送地图")
        self.map_data.setdefault("description", "校园配送点位、建筑物与飞行连线配置")
        self.map_data.setdefault("points", [])
        self.map_data.setdefault("connections", [])
        self.map_data.setdefault("buildings", [])
        return self.map_data

    def save_campus_map(self, data):
        """保存完整校园地图配置，点位和建筑物以高德经纬度为准。"""
        payload = self._normalize_campus_map(data)
        payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(self.map_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        self.map_data = payload
        self.points = payload["points"]
        return payload

    def get_available_points(self):
        """返回当前配送范围内允许参与调度的点位。"""
        if not self.has_service_area():
            return list(self.points)

        available_points = []
        for point in self.points:
            if point.get("type") == "depot" or self.is_point_in_service_area(point):
                available_points.append(point)
        return available_points

    def get_allowed_location_names(self):
        """返回当前允许的地点名称列表。"""
        return [point["name"] for point in self.get_available_points()]

    def get_point_by_name(self, name):
        """根据地点名称查找对应点位。找不到时返回 None。"""
        for point in self.points:
            if point.get("name") == name:
                return point
        return None

    def get_distance(self, from_name, to_name):
        """根据坐标计算两点的直线距离。"""
        p1 = self.get_point_by_name(from_name)
        p2 = self.get_point_by_name(to_name)
        if not p1 or not p2:
            return None

        dx = float(p1["x"]) - float(p2["x"])
        dy = float(p1["y"]) - float(p2["y"])
        return (dx ** 2 + dy ** 2) ** 0.5

    def is_location_in_service_area(self, location_name):
        """按地点名判断是否在配送范围内。"""
        point = self.get_point_by_name(location_name)
        if point is None:
            return not self.has_service_area()
        return self.is_point_in_service_area(point)

    def is_point_in_service_area(self, point):
        """按项目平面坐标判断点是否在配送范围内。"""
        if not self.has_service_area():
            return True

        return self.is_point_in_polygon(
            x=float(point["x"]),
            y=float(point["y"]),
            polygon_points=self.service_area["points"],
            x_key="x",
            y_key="y",
        )

    def is_geocode_in_service_area(self, longitude, latitude):
        """按真实经纬度判断点是否在配送范围内。"""
        if not self.has_service_area():
            return True

        polygon_points = self.service_area["points"]
        if not polygon_points or any("lng" not in point or "lat" not in point for point in polygon_points):
            return False

        return self.is_point_in_polygon(
            x=float(longitude),
            y=float(latitude),
            polygon_points=polygon_points,
            x_key="lng",
            y_key="lat",
        )

    def is_point_in_polygon(self, x, y, polygon_points, x_key="x", y_key="y"):
        """使用射线法判断点是否在多边形内；边界视为范围内。"""
        if len(polygon_points) < 3:
            return False

        inside = False
        total = len(polygon_points)
        for index in range(total):
            current = polygon_points[index]
            previous = polygon_points[index - 1]

            x1 = float(previous[x_key])
            y1 = float(previous[y_key])
            x2 = float(current[x_key])
            y2 = float(current[y_key])

            if self._is_point_on_segment(x, y, x1, y1, x2, y2):
                return True

            crosses = ((y1 > y) != (y2 > y))
            if not crosses:
                continue

            cross_x = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
            if x < cross_x:
                inside = not inside

        return inside

    def _is_point_on_segment(self, x, y, x1, y1, x2, y2, tolerance=1e-6):
        """判断点是否落在线段上。"""
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) > tolerance:
            return False

        dot = (x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)
        if dot < 0:
            return False

        squared_length = (x2 - x1) ** 2 + (y2 - y1) ** 2
        return dot <= squared_length + tolerance

    def _normalize_vertex(self, point):
        """规范化前端提交的多边形顶点。"""
        if not isinstance(point, dict):
            raise ValueError("配送范围顶点必须是对象")

        normalized = {
            "x": float(point["x"]),
            "y": float(point["y"]),
        }

        if "lat" in point and point["lat"] is not None:
            normalized["lat"] = float(point["lat"])
        if "lng" in point and point["lng"] is not None:
            normalized["lng"] = float(point["lng"])

        return normalized

    def _validate_service_area_payload(self, payload):
        """校验配送范围数据结构。"""
        if not isinstance(payload, dict):
            raise ValueError("配送范围数据必须是对象")

        points = payload.get("points")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("配送范围至少需要 3 个顶点")

        for point in points:
            if not isinstance(point, dict):
                raise ValueError("配送范围顶点必须是对象")
            if "x" not in point or "y" not in point:
                raise ValueError("配送范围顶点必须包含 x 和 y")
            float(point["x"])
            float(point["y"])

    def _empty_campus_map(self):
        return {
            "name": "校园配送地图",
            "description": "校园配送点位、建筑物与飞行连线配置",
            "points": [],
            "connections": [],
            "buildings": [],
        }

    def _normalize_campus_map(self, data):
        if not isinstance(data, dict):
            raise ValueError("校园地图数据必须是对象")
        if not isinstance(data.get("points", []), list):
            raise ValueError("points 必须是列表")
        if not isinstance(data.get("connections", []), list):
            raise ValueError("connections 必须是列表")
        if not isinstance(data.get("buildings", []), list):
            raise ValueError("buildings 必须是列表")

        return {
            "name": data.get("name") or "校园配送地图",
            "description": data.get("description") or "校园配送点位、建筑物与飞行连线配置",
            "points": [self._normalize_campus_point(point) for point in data.get("points", [])],
            "connections": [self._normalize_connection(item) for item in data.get("connections", [])],
            "buildings": [self._normalize_building(item) for item in data.get("buildings", [])],
        }

    def _normalize_campus_point(self, point):
        if not isinstance(point, dict):
            raise ValueError("配送点必须是对象")

        name = str(point.get("name", "")).strip()
        if not name:
            raise ValueError("配送点必须包含 name")

        normalized = {
            "id": str(point.get("id") or name),
            "name": name,
            "type": str(point.get("type") or "delivery"),
            "lat": float(point["lat"]),
            "lng": float(point["lng"]),
        }
        normalized["x"] = float(point.get("x", self._lng_to_x(normalized["lng"])))
        normalized["y"] = float(point.get("y", self._lat_to_y(normalized["lat"])))

        if "altitude" in point and point["altitude"] not in (None, ""):
            normalized["altitude"] = float(point["altitude"])

        return normalized

    def _normalize_connection(self, connection):
        if not isinstance(connection, dict):
            raise ValueError("飞行连线必须是对象")

        normalized = {
            "from": str(connection.get("from", "")).strip(),
            "to": str(connection.get("to", "")).strip(),
        }
        if not normalized["from"] or not normalized["to"]:
            raise ValueError("飞行连线必须包含 from 和 to")

        if "flight_altitude" in connection and connection["flight_altitude"] not in (None, ""):
            normalized["flight_altitude"] = float(connection["flight_altitude"])

        return normalized

    def _normalize_building(self, building):
        if not isinstance(building, dict):
            raise ValueError("建筑物必须是对象")

        name = str(building.get("name", "")).strip()
        if not name:
            raise ValueError("建筑物必须包含 name")

        polygon = building.get("polygon")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError("建筑物轮廓至少需要 3 个顶点")

        return {
            "id": str(building.get("id") or name),
            "name": name,
            "height": float(building["height"]),
            "polygon": [self._normalize_lnglat_vertex(point) for point in polygon],
        }

    def _normalize_lnglat_vertex(self, point):
        if not isinstance(point, dict):
            raise ValueError("经纬度顶点必须是对象")
        return {
            "lat": float(point["lat"]),
            "lng": float(point["lng"]),
        }

    def _lng_to_x(self, lng):
        return ((float(lng) - 116.35) / 0.1) * 1000

    def _lat_to_y(self, lat):
        return ((float(lat) - 39.88) / 0.05) * 1000

    def _create_demo_map(self):
        """创建演示地图。真实地图不可用时，程序仍然可以继续运行。"""
        self.points = [
            {"name": "校医院", "x": 500, "y": 400, "type": "depot"},
            {"name": "宿舍A区", "x": 300, "y": 200, "type": "delivery"},
            {"name": "宿舍B区", "x": 700, "y": 200, "type": "delivery"},
            {"name": "教学楼", "x": 200, "y": 500, "type": "delivery"},
            {"name": "体育馆", "x": 800, "y": 500, "type": "delivery"},
            {"name": "图书馆", "x": 500, "y": 600, "type": "delivery"},
        ]
        self.map_data = {"points": self.points}
        print("[MAP] 使用演示版地图数据")


map_service = MapService()
