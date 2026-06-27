# genetic_planner.py - LLM priority constraints + GA route planner

import math
import random


class GeneticPlanner:
    """Plan task order with a genetic algorithm over map point coordinates."""

    def __init__(
        self,
        pop_size=50,
        max_gen=100,
        crossover_rate=0.8,
        mutation_rate=0.1,
        lam=50,
        flight_altitude_limit=12,
    ):
        self.pop_size = pop_size
        self.max_gen = max_gen
        self.crossover_rate = crossover_rate
        self.mutation_rate = mutation_rate
        self.lam = lam
        self.flight_altitude_limit = flight_altitude_limit
        self.priority_values = {"高": 3, "中": 2, "低": 1}

    def plan_route(
        self,
        tasks,
        map_points,
        buildings=None,
        priority_constraints=None,
        natural_language_analysis=None,
    ):
        obstacle_buildings = self._normalize_obstacle_buildings(buildings or [])
        if not tasks or not map_points:
            depot_name = self._get_depot_name(map_points)
            return {
                "planning_basis": "ga",
                "natural_language_understanding": natural_language_analysis or {},
                "priority_constraints": priority_constraints or {},
                "route_summary": {
                    "total_distance": 0,
                    "priority_penalty": 0,
                    "fitness_cost": 0,
                    "start_point": depot_name,
                    "end_point": depot_name,
                    "flight_altitude_limit": self.flight_altitude_limit,
                    "obstacle_count": 0,
                },
                "tasks_order": [],
                "flight_path": [],
                "waypoints": [],
                "note": "当前没有可规划的任务。",
            }

        point_lookup = {point["name"]: point for point in map_points}
        depot_name = self._get_depot_name(map_points)
        depot = point_lookup.get(depot_name)
        valid_tasks = [task for task in tasks if task.get("location") in point_lookup]

        if not depot:
            raise ValueError("地图中缺少起点/仓库点")
        if not valid_tasks:
            raise ValueError("待配送订单没有匹配的地图点位")

        ordered_tasks, planner_note = self._plan_task_order(
            valid_tasks,
            point_lookup,
            depot,
            obstacle_buildings,
            priority_constraints or {},
        )
        waypoints = [self._build_waypoint(0, depot)]
        tasks_order = []

        for index, task in enumerate(ordered_tasks, start=1):
            point = point_lookup[task["location"]]
            effective_priority = self._task_priority(task, priority_constraints or {})
            tasks_order.append(
                {
                    "order_id": task["id"],
                    "medicine": task["medicine"],
                    "location": task["location"],
                    "priority": task["priority"],
                    "effective_priority": effective_priority,
                    "order": index,
                    "reason": self._build_reason(task, effective_priority, priority_constraints or {}),
                }
            )
            waypoints.append(self._build_waypoint(index, point))

        waypoints.append(self._build_waypoint(len(waypoints), depot))
        flight_path = self._build_flight_path(waypoints, obstacle_buildings)
        total_distance = sum(segment["distance"] for segment in flight_path)
        blocked_segments = sum(1 for segment in flight_path if segment.get("requires_detour"))
        priority_penalty = self._priority_penalty(ordered_tasks, priority_constraints or {})

        return {
            "planning_basis": "llm_priority_ga",
            "natural_language_understanding": natural_language_analysis or {},
            "priority_constraints": priority_constraints or {},
            "route_summary": {
                "total_distance": total_distance,
                "priority_penalty": priority_penalty,
                "fitness_cost": round(total_distance + self.lam * priority_penalty, 2),
                "start_point": depot_name,
                "end_point": depot_name,
                "flight_altitude_limit": self.flight_altitude_limit,
                "obstacle_count": blocked_segments,
            },
            "tasks_order": tasks_order,
            "flight_path": flight_path,
            "waypoints": waypoints,
            "note": planner_note,
        }

    def _plan_task_order(self, tasks, point_lookup, depot, obstacle_buildings, priority_constraints):
        if len(tasks) == 1:
            return list(tasks), "仅 1 个任务，无需遗传迭代。"

        population = self._initial_population(tasks)
        best_order = None
        best_cost = float("inf")

        for _ in range(self.max_gen):
            scored = [
                (
                    self._fitness_cost(
                        individual,
                        point_lookup,
                        depot,
                        obstacle_buildings,
                        priority_constraints,
                    ),
                    individual,
                )
                for individual in population
            ]
            scored.sort(key=lambda item: item[0])
            if scored[0][0] < best_cost:
                best_cost = scored[0][0]
                best_order = list(scored[0][1])

            selected = self._select_population(scored)
            next_population = [list(scored[0][1])]

            while len(next_population) < self.pop_size:
                parent_a = random.choice(selected)
                parent_b = random.choice(selected)
                if random.random() < self.crossover_rate:
                    child_a, child_b = self._order_crossover(parent_a, parent_b)
                else:
                    child_a, child_b = list(parent_a), list(parent_b)

                next_population.append(self._swap_mutate(child_a))
                if len(next_population) < self.pop_size:
                    next_population.append(self._swap_mutate(child_b))

            population = next_population

        return best_order or list(tasks), f"遗传算法完成排序：种群 {self.pop_size}，迭代 {self.max_gen} 代。"

    def _initial_population(self, tasks):
        population = []
        for _ in range(self.pop_size):
            individual = list(tasks)
            random.shuffle(individual)
            population.append(individual)
        return population

    def _select_population(self, scored):
        ranked = [individual for _, individual in scored]
        elite_count = max(2, self.pop_size // 5)
        selected = [list(individual) for individual in ranked[:elite_count]]
        while len(selected) < self.pop_size:
            selected.append(list(random.choice(ranked[: max(elite_count * 2, 1)])))
        return selected

    def _order_crossover(self, parent_a, parent_b):
        size = len(parent_a)
        if size < 2:
            return list(parent_a), list(parent_b)

        start, end = sorted(random.sample(range(size), 2))
        return (
            self._make_ox_child(parent_a, parent_b, start, end),
            self._make_ox_child(parent_b, parent_a, start, end),
        )

    def _make_ox_child(self, primary, secondary, start, end):
        child = [None] * len(primary)
        child[start : end + 1] = primary[start : end + 1]
        used_ids = {task["id"] for task in child if task is not None}
        fill_items = [task for task in secondary if task["id"] not in used_ids]

        fill_index = 0
        for index, task in enumerate(child):
            if task is None:
                child[index] = fill_items[fill_index]
                fill_index += 1
        return child

    def _swap_mutate(self, individual):
        if len(individual) >= 2 and random.random() < self.mutation_rate:
            first, second = random.sample(range(len(individual)), 2)
            individual[first], individual[second] = individual[second], individual[first]
        return individual

    def _fitness_cost(self, order, point_lookup, depot, obstacle_buildings, priority_constraints):
        distance = self._total_distance(order, point_lookup, depot, obstacle_buildings)
        penalty = self._priority_penalty(order, priority_constraints)
        return distance + self.lam * penalty

    def _total_distance(self, order, point_lookup, depot, obstacle_buildings):
        distance = 0
        previous = depot
        for task in order:
            current = point_lookup[task["location"]]
            distance += self._segment_cost(previous, current, obstacle_buildings)["distance"]
            previous = current
        distance += self._segment_cost(previous, depot, obstacle_buildings)["distance"]
        return distance

    def _priority_penalty(self, order, priority_constraints):
        penalty = 0
        total = len(order)
        for index, task in enumerate(order):
            priority = self._task_priority(task, priority_constraints)
            value = self.priority_values.get(priority, 2)
            penalty += (total - index) * (4 - value)

        for earlier_index, earlier_task in enumerate(order):
            earlier_value = self.priority_values.get(self._task_priority(earlier_task, priority_constraints), 2)
            for later_task in order[earlier_index + 1:]:
                later_value = self.priority_values.get(self._task_priority(later_task, priority_constraints), 2)
                if earlier_value < later_value:
                    penalty += total * (later_value - earlier_value)

        return penalty

    def _task_priority(self, task, priority_constraints):
        location = task.get("location")
        return self._normalize_priority(priority_constraints.get(location) or task.get("priority"))

    def _normalize_priority(self, priority):
        if priority in self.priority_values:
            return priority
        return "中"

    def _build_flight_path(self, waypoints, obstacle_buildings):
        flight_path = []
        for index in range(len(waypoints) - 1):
            start = waypoints[index]
            end = waypoints[index + 1]
            segment = self._segment_cost(start, end, obstacle_buildings)
            flight_path.append(
                {
                    "from": start["name"],
                    "to": end["name"],
                    "distance": int(round(segment["distance"])),
                    "direct_distance": int(round(segment["direct_distance"])),
                    "flight_altitude": self.flight_altitude_limit,
                    "requires_detour": bool(segment["blocking_buildings"]),
                    "blocking_buildings": segment["blocking_buildings"],
                }
            )
        return flight_path

    def _build_waypoint(self, order, point):
        waypoint = {
            "order": order,
            "name": point["name"],
            "x": point["x"],
            "y": point["y"],
            "altitude": float(point.get("altitude", 0) or 0),
        }
        if "lat" in point:
            waypoint["lat"] = point["lat"]
        if "lng" in point:
            waypoint["lng"] = point["lng"]
        return waypoint

    def _segment_cost(self, start_point, end_point, obstacle_buildings):
        direct_distance = self._distance_between_points(start_point, end_point)
        blocking_buildings = self._find_blocking_buildings(start_point, end_point, obstacle_buildings)

        if not blocking_buildings:
            return {
                "distance": direct_distance,
                "direct_distance": direct_distance,
                "blocking_buildings": [],
            }

        detour_distance = direct_distance
        for building in blocking_buildings:
            detour_distance += self._estimate_detour_extra_distance(start_point, end_point, building)

        return {
            "distance": detour_distance,
            "direct_distance": direct_distance,
            "blocking_buildings": [building["name"] for building in blocking_buildings],
        }

    def _distance_between_points(self, start_point, end_point):
        dx = float(start_point["x"]) - float(end_point["x"])
        dy = float(start_point["y"]) - float(end_point["y"])
        start_altitude = float(start_point.get("altitude", 0) or 0)
        end_altitude = float(end_point.get("altitude", 0) or 0)
        cruise_climb = abs(self.flight_altitude_limit - start_altitude)
        cruise_descent = abs(self.flight_altitude_limit - end_altitude)
        dz = cruise_climb + cruise_descent
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _normalize_obstacle_buildings(self, buildings):
        normalized = []
        for building in buildings:
            try:
                height = float(building.get("height", 0) or 0)
            except (TypeError, ValueError):
                continue

            if height <= self.flight_altitude_limit:
                continue

            polygon = []
            for vertex in building.get("polygon", []):
                if "lat" not in vertex or "lng" not in vertex:
                    continue
                polygon.append(
                    {
                        "x": self._lng_to_x(vertex["lng"]),
                        "y": self._lat_to_y(vertex["lat"]),
                    }
                )

            if len(polygon) >= 3:
                normalized.append(
                    {
                        "name": building.get("name") or building.get("id") or "未命名建筑",
                        "height": height,
                        "polygon": polygon,
                    }
                )
        return normalized

    def _find_blocking_buildings(self, start_point, end_point, obstacle_buildings):
        blocking = []
        start = {"x": float(start_point["x"]), "y": float(start_point["y"])}
        end = {"x": float(end_point["x"]), "y": float(end_point["y"])}
        for building in obstacle_buildings:
            polygon = building["polygon"]
            if (
                self._point_in_polygon(start, polygon)
                or self._point_in_polygon(end, polygon)
                or self._segment_intersects_polygon(start, end, polygon)
            ):
                blocking.append(building)
        return blocking

    def _segment_intersects_polygon(self, start, end, polygon):
        for index, current in enumerate(polygon):
            previous = polygon[index - 1]
            if self._segments_intersect(start, end, previous, current):
                return True
        return False

    def _segments_intersect(self, a, b, c, d):
        def orientation(p, q, r):
            return (q["y"] - p["y"]) * (r["x"] - q["x"]) - (q["x"] - p["x"]) * (r["y"] - q["y"])

        def on_segment(p, q, r):
            return (
                min(p["x"], r["x"]) <= q["x"] <= max(p["x"], r["x"])
                and min(p["y"], r["y"]) <= q["y"] <= max(p["y"], r["y"])
            )

        o1 = orientation(a, b, c)
        o2 = orientation(a, b, d)
        o3 = orientation(c, d, a)
        o4 = orientation(c, d, b)
        tolerance = 1e-9

        if o1 * o2 < 0 and o3 * o4 < 0:
            return True
        if abs(o1) <= tolerance and on_segment(a, c, b):
            return True
        if abs(o2) <= tolerance and on_segment(a, d, b):
            return True
        if abs(o3) <= tolerance and on_segment(c, a, d):
            return True
        if abs(o4) <= tolerance and on_segment(c, b, d):
            return True
        return False

    def _point_in_polygon(self, point, polygon):
        inside = False
        for index, current in enumerate(polygon):
            previous = polygon[index - 1]
            crosses = ((previous["y"] > point["y"]) != (current["y"] > point["y"]))
            if not crosses:
                continue
            cross_x = (
                (current["x"] - previous["x"])
                * (point["y"] - previous["y"])
                / ((current["y"] - previous["y"]) or 1e-9)
                + previous["x"]
            )
            if point["x"] < cross_x:
                inside = not inside
        return inside

    def _estimate_detour_extra_distance(self, start_point, end_point, building):
        polygon = building["polygon"]
        xs = [point["x"] for point in polygon]
        ys = [point["y"] for point in polygon]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        clearance = 8
        return max(width, height) + clearance * 2

    def _lng_to_x(self, lng):
        return ((float(lng) - 116.35) / 0.1) * 1000

    def _lat_to_y(self, lat):
        return ((float(lat) - 39.88) / 0.05) * 1000

    def _build_reason(self, task, effective_priority, priority_constraints):
        if task.get("location") in priority_constraints:
            return f"LLM 解析该地点为{effective_priority}优先级，GA 综合距离和优先级惩罚后排序。"
        return f"沿用订单{effective_priority}优先级，GA 综合距离和优先级惩罚后排序。"

    def _get_depot_name(self, map_points):
        for point in map_points or []:
            if point.get("type") == "depot":
                return point["name"]
        return "校医院"


genetic_planner = GeneticPlanner()
