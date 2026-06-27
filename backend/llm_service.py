# llm_service.py - 豆包大模型服务

import json
import math

try:
    # [本次修改-依赖兜底] requests 缺失时不要让整个后端导入失败，本地规划仍可继续工作。
    import requests
except ImportError:
    requests = None

from config import Config


class LLMService:
    """大模型服务类 - 调用豆包API规划无人机路线"""

    def __init__(self):
        self.api_url = Config.DOUBAO_API_URL
        self.api_key = Config.DOUBAO_API_KEY

        self.model = Config.DOUBAO_MODEL
        self.endpoint_id = Config.DOUBAO_ENDPOINT_ID
        # [本次修改-终端兼容] 避免 Windows GBK 终端因 emoji 输出报错。
        print("[LLM] 豆包大模型服务已初始化")

    def is_configured(self):
        """[本次修改-豆包接入] 兼容两种调用方式：公共模型名或专属 Endpoint ID。"""
        return bool(self.api_key and (self.endpoint_id or self.model))

    def parse_priorities(self, tasks, map_points, natural_language_input=""):
        """Use LLM only for semantic priority extraction; GA handles ordering."""
        fallback = self._build_priority_fallback(tasks, map_points, natural_language_input)

        if not self.is_configured():
            fallback["planning_basis"] = "local_priority_fallback"
            fallback["note"] = "未配置豆包 API，已按订单优先级和本地规则生成优先级约束。"
            return fallback

        try:
            print("[LLM] 正在解析自然语言优先级...")
            prompt = self._build_priority_prompt(tasks, map_points, natural_language_input)
            response = self._call_doubao(prompt)
            parsed = self._parse_response(response)
            normalized = self._normalize_priority_result(parsed, tasks, fallback)
            if normalized:
                normalized["planning_basis"] = "llm_priority"
                normalized.setdefault("note", "LLM 已解析优先级，路线顺序由 GA 计算。")
                return normalized

            fallback["planning_basis"] = "local_priority_fallback"
            fallback["note"] = "豆包优先级解析结果不可用，已使用本地优先级兜底。"
            return fallback
        except Exception as exc:
            fallback["planning_basis"] = "local_priority_fallback"
            fallback["note"] = f"豆包优先级解析失败，已使用本地优先级兜底：{exc}"
            return fallback

    def _build_priority_prompt(self, tasks, map_points, natural_language_input):
        natural_language_text = natural_language_input.strip() or "默认策略：沿用订单紧急度，数字越高越优先。"
        payload = {
            "instruction": natural_language_text,
            "tasks": self._build_prompt_tasks(tasks),
            "points": self._build_prompt_points(tasks, map_points),
        }
        task_locations = [task["location"] for task in tasks]

        return (
            "你只负责解析无人机配送任务的语义优先级，不要规划路线，不要排序。\n"
            "返回纯JSON，不要markdown，不要代码块，不要解释。\n"
            "优先级只能是 1 到 10 的整数，数字越高表示越紧急。\n"
            "必须为每个任务地点都给出 priority_constraints；地点名只能来自 tasks 的 location。\n"
            "如果用户没有特别说明某地点，就沿用该订单原始紧急度。\n"
            "只返回这个结构："
            '{"reasoning":"1-2句话说明依据",'
            '"natural_language_understanding":{"raw_instruction":"","recognized_locations":[],"priority_policy":"","special_requirements":[]},'
            '"priority_constraints":{}}'
            f"\n任务地点：{json.dumps(task_locations, ensure_ascii=False)}"
            "\n输入数据:\n"
            f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _normalize_priority_result(self, parsed, tasks, fallback):
        if not isinstance(parsed, dict):
            return None

        raw_constraints = parsed.get("priority_constraints")
        if not isinstance(raw_constraints, dict):
            return None

        task_locations = [task["location"] for task in tasks]
        fallback_constraints = fallback.get("priority_constraints", {})
        constraints = {}
        for task in tasks:
            location = task["location"]
            constraints[location] = self._normalize_priority(
                raw_constraints.get(location) or fallback_constraints.get(location) or task.get("priority")
            )

        analysis = parsed.get("natural_language_understanding")
        if not isinstance(analysis, dict):
            analysis = fallback.get("natural_language_understanding", {})

        recognized = analysis.get("recognized_locations")
        if not isinstance(recognized, list):
            recognized = []
        analysis["recognized_locations"] = [
            name for name in recognized if name in task_locations
        ]
        analysis.setdefault("raw_instruction", fallback.get("natural_language_understanding", {}).get("raw_instruction", ""))
        analysis.setdefault("priority_policy", "LLM 解析自然语言优先级，GA 负责路线排序")
        analysis.setdefault("special_requirements", [])

        return {
            "reasoning": str(parsed.get("reasoning") or fallback.get("reasoning") or ""),
            "natural_language_understanding": analysis,
            "priority_constraints": constraints,
        }

    def _build_priority_fallback(self, tasks, map_points, natural_language_input):
        analysis = self._analyze_natural_language(natural_language_input, map_points)
        constraints = {
            task["location"]: self._normalize_priority(task.get("priority"))
            for task in tasks
        }

        text = (natural_language_input or "").strip()
        for location in analysis.get("recognized_locations", []):
            if location not in constraints:
                continue
            location_index = text.find(location)
            window = text[max(0, location_index - 8): location_index + len(location) + 12]
            if any(keyword in window for keyword in ("急", "优先", "先送", "先去", "马上", "立刻")):
                constraints[location] = "10"
            if any(keyword in window for keyword in ("不急", "最后", "晚点", "低优先")):
                constraints[location] = "1"

        return {
            "reasoning": "按订单原始紧急度生成约束；自然语言中明确提到的紧急或延后地点会被本地规则修正。",
            "natural_language_understanding": analysis,
            "priority_constraints": constraints,
        }

    def _normalize_priority(self, priority):
        legacy_map = {"高": "10", "中": "5", "低": "1"}
        if priority in legacy_map:
            return legacy_map[priority]
        try:
            value = int(priority)
        except (TypeError, ValueError):
            value = 5
        value = max(1, min(10, value))
        return str(value)

    def plan_route(self, tasks, map_points, natural_language_input=""):
        """[本次修改-自然语言规划] 两阶段方案：豆包只排订单顺序，路线细节由后端本地计算。"""
        fallback_route = self._build_local_route(tasks, map_points, natural_language_input)

        if not self.is_configured():
            fallback_route["planning_basis"] = "local_fallback"
            fallback_route["note"] = "未配置豆包 API Key / Endpoint，已使用本地兜底规划。"
            return fallback_route

        try:
            print("[LLM] 正在调用豆包API...")
            prompt = self._build_prompt(tasks, map_points, natural_language_input)
            response = self._call_doubao(prompt)
            if response:
                route = self._parse_response(response)
                normalized_route = self._normalize_route(
                    route=route,
                    tasks=tasks,
                    map_points=map_points,
                    natural_language_input=natural_language_input,
                    fallback_route=fallback_route,
                )
                if normalized_route:
                    normalized_route["planning_basis"] = "doubao"
                    normalized_route.setdefault("note", "路线由豆包生成，缺失字段已由后端补齐。")
                    return normalized_route

            print("[LLM] 豆包返回不可用，改用本地兜底规划")
            fallback_route["planning_basis"] = "local_fallback"
            fallback_route["note"] = "豆包返回结果不可解析，已使用本地兜底规划。"
            return fallback_route
        except Exception as e:
            print(f"[LLM] 规划失败: {e}，使用本地兜底规划")
            fallback_route["planning_basis"] = "local_fallback"
            fallback_route["note"] = f"豆包调用失败，已使用本地兜底规划：{e}"
            return fallback_route

    def _build_prompt(self, tasks, map_points, natural_language_input):
        """[本次修改-两阶段规划] 豆包只返回订单顺序与自然语言理解，最大限度缩短响应时间。"""
        natural_language_text = natural_language_input.strip() or "默认策略：紧急度数字高的任务优先，其次总距离最短。"
        prompt_payload = {
            "instruction": natural_language_text,
            "points": self._build_prompt_points(tasks, map_points),
            "tasks": self._build_prompt_tasks(tasks),
        }

        prompt = (
            "你负责给订单排序。返回纯JSON，不要markdown，不要代码块，不要解释。\n"
            "规则:"
            "1. 紧急度 10>9>...>1;"
            "2. 若 instruction 提到某地点，则在不违反优先级前提下优先;"
            "3. 尽量让相邻地点距离更短;"
            "4. 只能使用 tasks 里的订单 id。\n"
            "只返回这个结构:"
            '{"natural_language_understanding":{"raw_instruction":"","recognized_locations":[],"priority_policy":"","special_requirements":[]},'
            '"task_order_ids":[1,2,3],'
            '"ordering_reasons":["","",""]}'
            "\n输入数据:\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        return prompt

    def _build_prompt_tasks(self, tasks):
        """[本次修改-两阶段规划] 把任务压缩成更短的结构，减少模型阅读负担。"""
        compact_tasks = []
        for task in tasks:
            compact_tasks.append(
                {
                    "id": task["id"],
                    "m": task["medicine"],
                    "l": task["location"],
                    "p": task["priority"],
                    "n": (task.get("notes") or "")[:20],
                }
            )
        return compact_tasks

    def _build_prompt_points(self, tasks, map_points):
        """[本次修改-两阶段规划] 只提供起点和任务涉及地点，避免把无关点位塞给模型。"""
        relevant_names = {self._get_depot_name(map_points)}
        for task in tasks:
            relevant_names.add(task["location"])

        compact_points = []
        for point in map_points:
            if point["name"] in relevant_names:
                compact_points.append(
                    {
                        "name": point["name"],
                        "x": point["x"],
                        "y": point["y"],
                        "type": point.get("type", "unknown"),
                    }
                )
        return compact_points

    def _call_doubao(self, prompt):
        """调用豆包API（按实测结果设置更合理的超时时间）"""
        # [本次修改-依赖兜底] 如果没有安装 requests，就直接返回 None，交给本地兜底规划接管。
        if requests is None:
            print("[LLM] 未安装 requests，无法调用豆包API，改用本地兜底规划")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        model_name = self.endpoint_id or self.model
        data = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "你是路径规划助手。输出纯JSON，禁止解释、禁止markdown、禁止代码块。",
                },
                {"role": "user", "content": prompt},
            ],
            # [本次修改-两阶段规划] 再次缩短输出长度，只允许返回非常小的 JSON。
            "temperature": 0,
            "max_tokens": 220,
        }

        try:
            # [本次修改-超时调整] 实测复杂规划约 27 秒返回，这里设置为 60 秒，兼顾成功率与等待体验。
            response = requests.post(self.api_url, headers=headers, json=data, timeout=60)

            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                print("[LLM] 豆包API调用成功")
                return content

            print(f"[LLM] API调用失败: {response.status_code} {response.text}")
            return None
        except requests.exceptions.Timeout:
            print("[LLM] 豆包API超时（60秒），使用本地兜底规划")
            return None
        except Exception as e:
            print(f"[LLM] 请求异常: {e}")
            return None

    def _parse_response(self, response_text):
        """解析大模型返回的JSON"""
        if response_text is None:
            return None

        try:
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.find("```", start)
                response_text = response_text[start:end].strip()
            elif "```" in response_text:
                start = response_text.find("```") + 3
                end = response_text.find("```", start)
                response_text = response_text[start:end].strip()

            return json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"[LLM] JSON解析失败: {e}")
            return None

    def _normalize_route(self, route, tasks, map_points, natural_language_input, fallback_route):
        """[本次修改-两阶段规划] 根据豆包返回的订单顺序，本地生成完整路线结果。"""
        if not isinstance(route, dict):
            return None

        normalized_route = json.loads(json.dumps(route, ensure_ascii=False))
        point_lookup = {point["name"]: point for point in map_points}
        depot_name = self._get_depot_name(map_points)
        ordered_tasks = self._normalize_task_order_ids(
            route=normalized_route,
            tasks=tasks,
            fallback_tasks=fallback_route.get("tasks_order", []),
        )
        if not ordered_tasks:
            return None

        waypoints = [self._build_waypoint(0, point_lookup[depot_name])]
        for index, task in enumerate(ordered_tasks, start=1):
            point = point_lookup.get(task["location"])
            if not point:
                return None
            waypoints.append(self._build_waypoint(index, point))
        waypoints.append(self._build_waypoint(len(waypoints), point_lookup[depot_name]))

        normalized_route["tasks_order"] = ordered_tasks
        normalized_route["waypoints"] = waypoints
        normalized_route["flight_path"] = self._build_flight_path(waypoints)
        total_distance = sum(segment["distance"] for segment in normalized_route["flight_path"])
        normalized_route["route_summary"] = {
            "total_distance": total_distance,
            "start_point": depot_name,
            "end_point": depot_name,
        }
        fallback_analysis = fallback_route.get("natural_language_understanding", {})
        normalized_route["natural_language_understanding"] = normalized_route.get(
            "natural_language_understanding",
            fallback_analysis,
        )
        return normalized_route

    def _normalize_task_order_ids(self, route, tasks, fallback_tasks):
        """[本次修改-两阶段规划] 解析豆包返回的 order ids，并还原为完整 tasks_order。"""
        task_lookup = {task["id"]: task for task in tasks}
        task_order_ids = route.get("task_order_ids")
        ordering_reasons = route.get("ordering_reasons") or []
        if not isinstance(task_order_ids, list) or not task_order_ids:
            return fallback_tasks

        normalized_tasks = []
        used_ids = set()
        for index, task_id in enumerate(task_order_ids, start=1):
            if task_id in used_ids or task_id not in task_lookup:
                continue
            matched_task = task_lookup[task_id]
            used_ids.add(task_id)
            reason = ordering_reasons[index - 1] if index - 1 < len(ordering_reasons) else "豆包按优先级和地点要求完成排序。"
            normalized_tasks.append(
                {
                    "order_id": matched_task["id"],
                    "medicine": matched_task["medicine"],
                    "location": matched_task["location"],
                    "priority": matched_task["priority"],
                    "order": index,
                    "reason": reason,
                }
            )

        if len(normalized_tasks) != len(tasks):
            remaining_tasks = [task for task in tasks if task["id"] not in used_ids]
            next_order = len(normalized_tasks) + 1
            for task in remaining_tasks:
                normalized_tasks.append(
                    {
                        "order_id": task["id"],
                        "medicine": task["medicine"],
                        "location": task["location"],
                        "priority": task["priority"],
                        "order": next_order,
                        "reason": "豆包未返回该订单顺序，后端已自动追加。",
                    }
                )
                next_order += 1

        return normalized_tasks or fallback_tasks

    def _build_local_route(self, tasks, map_points, natural_language_input):
        """[本次修改-本地兜底] 当豆包不可用时，用启发式规则完成基础路线规划。"""
        if not tasks or not map_points:
            depot_name = self._get_depot_name(map_points)
            return {
                "planning_basis": "local_fallback",
                "natural_language_understanding": {
                    "raw_instruction": natural_language_input,
                    "recognized_locations": [],
                    "priority_policy": "无任务可规划",
                    "special_requirements": [],
                },
                "route_summary": {
                    "total_distance": 0,
                    "start_point": depot_name,
                    "end_point": depot_name,
                },
                "tasks_order": [],
                "flight_path": [],
                "waypoints": [],
                "note": "当前没有可规划的任务。",
            }

        analysis = self._analyze_natural_language(natural_language_input, map_points)
        point_lookup = {point["name"]: point for point in map_points}
        depot_name = self._get_depot_name(map_points)
        current_name = depot_name
        remaining_tasks = list(tasks)
        ordered_tasks = []

        while remaining_tasks:
            next_task = min(
                remaining_tasks,
                key=lambda task: self._task_sort_key(
                    task=task,
                    current_name=current_name,
                    point_lookup=point_lookup,
                    analysis=analysis,
                ),
            )
            ordered_tasks.append(next_task)
            remaining_tasks.remove(next_task)
            current_name = next_task["location"]

        waypoints = [self._build_waypoint(0, point_lookup[depot_name])]
        tasks_order = []
        for index, task in enumerate(ordered_tasks, start=1):
            tasks_order.append(
                {
                    "order_id": task["id"],
                    "medicine": task["medicine"],
                    "location": task["location"],
                    "priority": task["priority"],
                    "order": index,
                    "reason": self._build_local_reason(task, analysis),
                }
            )
            point = point_lookup.get(task["location"])
            if point:
                waypoints.append(self._build_waypoint(index, point))

        waypoints.append(self._build_waypoint(len(waypoints), point_lookup[depot_name]))
        flight_path = self._build_flight_path(waypoints)
        total_distance = sum(segment["distance"] for segment in flight_path)

        return {
            "planning_basis": "local_fallback",
            "natural_language_understanding": analysis,
            "route_summary": {
                "total_distance": total_distance,
                "start_point": depot_name,
                "end_point": depot_name,
            },
            "tasks_order": tasks_order,
            "flight_path": flight_path,
            "waypoints": waypoints,
        }

    def _analyze_natural_language(self, natural_language_input, map_points):
        """[本次修改-自然语言理解] 用轻量规则先识别地点和调度偏好，供本地兜底和豆包提示共同使用。"""
        text = (natural_language_input or "").strip()
        recognized_locations = []

        for point in map_points:
            point_name = point["name"]
            if point_name in text:
                recognized_locations.append(
                    {
                        "name": point_name,
                        "position": text.find(point_name),
                    }
                )

        recognized_locations.sort(key=lambda item: item["position"])
        ordered_location_names = [item["name"] for item in recognized_locations]

        special_requirements = []
        if text:
            if "优先" in text or "先送" in text or "先去" in text:
                special_requirements.append("存在显式优先配送要求")
            if "最短" in text or "最近" in text or "顺路" in text:
                special_requirements.append("希望兼顾距离最短")
            if "返回" in text or "回到" in text:
                special_requirements.append("要求任务后返回起点")

        if ordered_location_names:
            priority_policy = f"紧急度数字越高越优先，同时优先关注用户提到的地点：{'、'.join(ordered_location_names)}"
        elif text:
            priority_policy = "紧急度数字越高越优先，并尽量满足用户的自然语言调度要求"
        else:
            priority_policy = "紧急度数字越高越优先，其次按距离缩短总航程"

        return {
            "raw_instruction": text,
            "recognized_locations": ordered_location_names,
            "priority_policy": priority_policy,
            "special_requirements": special_requirements,
        }

    def _task_sort_key(self, task, current_name, point_lookup, analysis):
        """[本次修改-本地兜底] 按“任务紧急度 > 用户点名地点 > 当前点距离”排序。"""
        recognized_locations = analysis.get("recognized_locations", [])
        default_rank = len(recognized_locations) + 1

        if task["location"] in recognized_locations:
            location_rank = recognized_locations.index(task["location"])
        else:
            location_rank = default_rank

        distance_rank = self._distance_between_names(current_name, task["location"], point_lookup)
        return (
            10 - int(self._normalize_priority(task.get("priority"))),
            location_rank,
            distance_rank,
            task["id"],
        )

    def _build_local_reason(self, task, analysis):
        """[本次修改-本地兜底] 给本地规划生成可读解释，便于前端展示。"""
        reason_parts = [f"紧急度 {self._normalize_priority(task.get('priority'))}/10"]
        recognized_locations = analysis.get("recognized_locations", [])
        if task["location"] in recognized_locations:
            reason_parts.append("用户在自然语言中明确提到了该地点")
        if not analysis.get("raw_instruction"):
            reason_parts.append("按默认最近路线策略排序")
        return "，".join(reason_parts)

    def _build_flight_path(self, waypoints):
        """[本次修改-结果兜底] 根据航点自动计算每一段飞行距离与时间。"""
        flight_path = []
        for index in range(len(waypoints) - 1):
            start = waypoints[index]
            end = waypoints[index + 1]
            distance = self._distance_between_points(start, end)
            flight_path.append(
                {
                    "from": start["name"],
                    "to": end["name"],
                    "distance": distance,
                }
            )
        return flight_path

    def _build_waypoint(self, order, point):
        """[本次修改-结果兜底] 统一航点结构。"""
        return {
            "order": order,
            "name": point["name"],
            "x": point["x"],
            "y": point["y"],
        }

    def _distance_between_names(self, from_name, to_name, point_lookup):
        from_point = point_lookup.get(from_name)
        to_point = point_lookup.get(to_name)
        if not from_point or not to_point:
            return 10**9
        return self._distance_between_points(from_point, to_point)

    def _distance_between_points(self, start_point, end_point):
        dx = start_point["x"] - end_point["x"]
        dy = start_point["y"] - end_point["y"]
        return int(round(math.sqrt(dx * dx + dy * dy)))

    def _get_depot_name(self, map_points):
        for point in map_points or []:
            if point.get("type") == "depot":
                return point["name"]
        return "校医院"


# 创建全局服务实例
llm_service = LLMService()
