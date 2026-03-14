#!/usr/bin/env python3
# iw_hub_controller.py

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Empty
import networkx as nx
import json
import threading
import time
from functools import partial
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db

class IWHubMissionController(Node):
    def __init__(self):
        super().__init__('fleet_path_manager_v39')
        self.publisher_ = self.create_publisher(String, '/robot_missions', 10)
        self.path_pub = self.create_publisher(String, '/robot_paths', 10)
        self.status_pub = self.create_publisher(String, '/robot_status', 10)
        self.order_completed_pub = self.create_publisher(String, '/order_completed', 10)
        self.subscription = self.create_subscription(String, '/robot_feedback', self.feedback_callback, 10)
        
        # --- [추가] UR_10_02(파레트 적재) 통신용 토픽 ---
        self.is_reached_pallet = {i: 0 for i in range(1, 13)} 
        self.ur10_pub = self.create_publisher(String, '/ur10_task_queue', 10)
        self.ur10_sub = self.create_subscription(String, '/ur10_task_done', self.ur10_done_callback, 10)

        # --- [추가] UR_10_01(유저 배송 하차 및 컨베이어) 통신용 토픽 ---
        self.ur10_user_pub = self.create_publisher(String, '/ur10_user_queue', 10)
        self.ur10_user_sub = self.create_subscription(String, '/ur10_user_done', self.ur10_user_done_callback, 10)

        self.user_services = []
        for i in range(1, 5):
            srv = self.create_service(Empty, f'/user_{i}_ready', partial(self.user_ready_cb, uid=i))
            self.user_services.append(srv)
            
        try:
            cred = credentials.Certificate('/home/rokey/IsaacSim-ros_workspaces/humble_ws/src/eium/eium/serviceAccountKey.json')
            firebase_admin.initialize_app(cred, {'databaseURL': 'https://rokeysmarthub-default-rtdb.asia-southeast1.firebasedatabase.app/'}) 
            self.get_logger().info("Firebase initialized successfully.")
        except Exception as e:
            self.get_logger().error(f"Firebase Init Error: {e}")

        self.G = self._build_map()
        self.gateways = self._map_gateways()
        self.reverse_gateways = {v: k for k, v in self.gateways.items()}
        
        self.robots = {i: {
            "last_node": f"robot_spawn_{i}", 
            "full_path": [], 
            "current_idx": 0, 
            "current_lock": [f"robot_spawn_{i}"],
            "load": "None",
            "target_p": None,
            "target_u": None,
            "current_task": None,
            "phase": "IDLE",
            "waiting_for_feedback": False
        } for i in range(1, 6)}
        
        self.task_queue = []
        self.replenish_queue = [] 
        self.replenishing_items = set() 
        
        self.current_stocks = {i: 4 for i in range(1, 13)}

        self.order_tracker = {}
        self.processed_orders = set()
        self.user_locked = {1: False, 2: False, 3: False, 4: False}
        self.lock = threading.Lock()

        threading.Thread(target=self._firebase_sync_loop, daemon=True).start()
        threading.Thread(target=self._control_loop, daemon=True).start()
        threading.Thread(target=self._firebase_replenish_loop, daemon=True).start()

    # UR_10_02 작업 완료 콜백 (파레트 -> AGV 적재 완료)
    def ur10_done_callback(self, msg):
        data = json.loads(msg.data)
        p_id = data.get("pallet_id")
        
        if p_id:
            with self.lock:
                self.is_reached_pallet[p_id] = 0  
                self.get_logger().info(f"UR10_02 파레트 {p_id} 작업 완료. AGV를 유저에게 보냅니다.")

                threading.Thread(target=self._decrement_firebase_stock, args=(p_id,)).start()
                
                for r_id, bot in self.robots.items():
                    if bot["phase"] == "WAITING_FOR_UR10" and bot["target_p"] == f"item_{p_id:02d}":
                        bot["load"] = "Carrying"
                        task = bot["current_task"]
                        
                        if task:
                            oid = task["oid"]
                            idx = task["item_idx"]
                            t_qty = task["total_qty"]
                            c_qty = task["current_qty"]
                            fb_msg = "적재 완료" if c_qty == t_qty else f"{t_qty}개중에 {c_qty}개 적재 중"
                            threading.Thread(target=self._update_fb_status, args=(f"Order/{oid}/items/{idx}", fb_msg)).start()

                        bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], bot["target_u"])
                        bot["current_idx"] = 0
                        bot["phase"] = "TO_USER"
                        self._broadcast(r_id, bot)
                        break

    # UR_10_01 작업 완료 콜백 (AGV 하차 및 컨베이어 완료)
    def ur10_user_done_callback(self, msg):
        data = json.loads(msg.data)
        r_id = data.get("robot_id")
        
        if r_id:
            with self.lock:
                bot = self.robots[r_id]
                if bot["phase"] == "WAITING_FOR_UR10_USER":
                    self.get_logger().info(f"UR10_01 유저 하차 완료 (AGV {r_id}). 다음 작업을 탐색합니다.")
                    bot["load"] = "None"
                    bot["current_task"] = None
                    
                    next_task = self._get_next_available_task()
                    if next_task:
                        p = next_task["item_id"]
                        bot["target_p"] = p
                        bot["target_u"] = f"user_{next_task['uid']}"
                        bot["phase"] = "TO_PALETTE"
                        bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], p)
                        bot["current_idx"] = 0
                        bot["current_task"] = next_task                        
                        threading.Thread(target=self._update_fb_status, args=(f"Order/{next_task['oid']}", "상품 적재 중")).start()
                    else:
                        bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], f"robot_spawn_{r_id}")
                        bot["current_idx"] = 0
                        bot["phase"] = "TO_SPAWN"
                        
                    self._broadcast(r_id, bot)

    def _build_map(self):
        G = nx.Graph()
        for c in range(1, 13):
            for r in range(1, 6):
                G.add_node((c, r), pos=(6.0-(r-1)*3.0, 23.0-(c-1)*2.0))
                if c < 12: G.add_edge((c, r), (c+1, r), weight=1.0)
                if r < 5: G.add_edge((c, r), (c, r+1), weight=1.0)
                
        for i, pos in {1:(-6.0,0.0), 2:(-3.0,0.0), 3:(-6.0,24.0), 4:(-3.0,24.0)}.items():
            G.add_node(f"robot_spawn_{i}", pos=pos)
            ey = 1.0 if i<=2 else 23.0
            G.add_node(f"entry_{i}", pos=(pos[0], ey))
            G.add_edge(f"robot_spawn_{i}", f"entry_{i}", weight=1.0)
            tr = 5 if pos[0] == -6.0 else 4
            G.add_edge(f"entry_{i}", (12 if i<=2 else 1, tr), weight=1.0)
            
        for i in range(1, 5):
            u_node, u_target = f"user_{i}", (2+(i-1)*3, 1)
            G.add_node(u_node, pos=([8.0, 21.0], [8.0, 15.0], [8.0, 9.0], [8.0, 3.0])[i-1])
            G.add_edge(u_node, u_target, weight=1.0)
            
        for c in range(1, 13):
            p_node, p_target = f"item_{c:02d}", (c, 5)
            G.add_node(p_node, pos=(-8.0, 23.0-(c-1)*2.0))
            G.add_edge(p_node, p_target, weight=1.0)
            
        G.add_node("robot_spawn_5", pos=(-12.0, 0.0))
        prev_corridor = "robot_spawn_5"
        
        for c in range(12, 0, -1):
            y = 25.0 - c * 2.0
            mid_node = f"mid_{c:02d}"             
            p_node = f"palette_{c:02d}"           
            e_node = f"extra_{c:02d}"             
            t_node = f"temp_{c:02d}"              
            
            G.add_node(mid_node, pos=(-12.0, y))
            G.add_node(p_node, pos=(-9.5, y))     
            G.add_node(e_node, pos=(-14.5, y))    
            G.add_node(t_node, pos=(-12.0, y + 3.0)) 
            
            G.add_edge(prev_corridor, mid_node, weight=1.0)
            G.add_edge(mid_node, p_node, weight=1.0)
            G.add_edge(mid_node, e_node, weight=1.0)
            G.add_edge(mid_node, t_node, weight=1.0)
            
            prev_corridor = mid_node
            
        return G

    def _map_gateways(self):
        m = {f"user_{i}": (2+(i-1)*3, 1) for i in range(1, 5)}
        m.update({f"item_{c:02d}": (c, 5) for c in range(1, 13)})
        return m

    #################### 새로운 알고리즘 적용한 경우 #############
    def _get_dynamic_path(self, r_id, start, end):
        temp_G = self.G.copy()
        forbidden = set()        
        for rid, bot in self.robots.items():
            if rid != r_id:
                forbidden.update(bot["current_lock"])
                curr_other = bot["full_path"][bot["current_idx"]] if bot["full_path"] else bot["last_node"]
                if curr_other in self.gateways:
                    forbidden.add(self.gateways[curr_other])
        
        for node in forbidden:
            if node in temp_G and node != start:
                for neighbor in temp_G.neighbors(node):
                    temp_G[node][neighbor]['weight'] = 500.0

        for u, v in temp_G.edges():
            if isinstance(u, tuple) and isinstance(v, tuple):
                if u[1] == v[1]: 
                    if r_id % 2 == 0: 
                        if v[0] > u[0]: temp_G[u][v]['weight'] -= 0.1
                    else:
                        if v[0] < u[0]: temp_G[u][v]['weight'] -= 0.1
        try:
            return nx.shortest_path(temp_G, source=start, target=end, weight='weight')
        except: 
            return [start]
################################여기까지#######################
    def _update_fb_status(self, path, status_msg):
        try:
            db.reference(path).update({'status': status_msg})
        except Exception as e:
            pass
    def _decrement_firebase_stock(self, p_id):
        try:
            item_key = f"item_{p_id:02d}"
            ref = db.reference(f'products/{item_key}')
            item_data = ref.get()
            
            if item_data is not None:
                current_stock = int(item_data.get('stock', 0))
                if current_stock > 0:
                    new_stock = current_stock - 1
                    ref.update({'stock': new_stock})
                    self.get_logger().info(f"🔥 [Firebase] {item_key} 실제 재고 감소 완료: {current_stock} -> {new_stock}")
        except Exception as e:
            self.get_logger().error(f"Firebase Stock Update Error: {e}")

    def user_ready_cb(self, request, response, uid):
        with self.lock:
            self.user_locked[uid] = False
        return response

    def _get_next_available_task(self):
        for i, task in enumerate(self.task_queue):
            if not self.user_locked[task["uid"]]:
                return self.task_queue.pop(i)
        return None

    def _send_lift_command(self, r_id, action):
        msg_data = {"robot_id": r_id, "lift": action}
        self.publisher_.publish(String(data=json.dumps(msg_data)))

    def feedback_callback(self, msg):
        data = json.loads(msg.data)
        r_id = data["robot_id"]
        
        with self.lock:
            bot = self.robots[r_id]
            bot["waiting_for_feedback"] = False

            # 5번 로봇(재고 보충) 리프트 로직 유지
            if bot["phase"].startswith("LIFT_"):
                if bot["phase"] == "LIFT_UP_MAIN":
                    bot["phase"] = "REP_TO_TEMP"
                    p_id = bot["current_task"]["p_id"]
                    bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], f"temp_{p_id:02d}")
                    bot["current_idx"] = 0
                
                elif bot["phase"] == "LIFT_DOWN_TEMP":
                    bot["phase"] = "REP_TO_EXTRA"
                    p_id = bot["current_task"]["p_id"]
                    bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], f"extra_{p_id:02d}")
                    bot["current_idx"] = 0

                elif bot["phase"] == "LIFT_UP_EXTRA":
                    bot["phase"] = "REP_TO_MAIN_2"
                    p_id = bot["current_task"]["p_id"]
                    bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], f"palette_{p_id:02d}")
                    bot["current_idx"] = 0
                
                elif bot["phase"] == "LIFT_DOWN_MAIN_2":
                    bot["phase"] = "REP_TO_TEMP_2"
                    p_id = bot["current_task"]["p_id"]
                    bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], f"temp_{p_id:02d}")
                    bot["current_idx"] = 0

                elif bot["phase"] == "LIFT_UP_TEMP_2":
                    bot["phase"] = "REP_TO_EXTRA_2"
                    p_id = bot["current_task"]["p_id"]
                    bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], f"extra_{p_id:02d}")
                    bot["current_idx"] = 0

                elif bot["phase"] == "LIFT_DOWN_EXTRA_2":
                    bot["phase"] = "TO_SPAWN"
                    p_id = bot["current_task"]["p_id"]
                    bot["full_path"] = self._get_dynamic_path(r_id, bot["last_node"], "robot_spawn_5")
                    bot["current_idx"] = 0
                    
                    w_stock = bot["current_task"]["waiting_stock"]
                    item_key = bot["current_task"]["item_key"]
                    add_qty = min(4, w_stock)
                    new_stock = add_qty
                    new_waiting = w_stock - add_qty
                    try:
                        db.reference(f'products/{item_key}').update({'stock': new_stock, 'Waiting Stock': new_waiting})
                    except: pass
                    if p_id in self.replenishing_items:
                        self.replenishing_items.remove(p_id)
                    bot["current_task"] = None
                
                self._broadcast(r_id, bot)
                return 

            if not bot["full_path"]: return

            if len(bot["current_lock"]) > 1:
                bot["current_lock"].pop(0)

            bot["current_idx"] += 1
            curr_node = bot["full_path"][bot["current_idx"]]

            # 도착했을 때 처리
            if bot["current_idx"] == len(bot["full_path"]) - 1:
                
                bot["last_node"] = curr_node 
                
                if r_id == 5:
                    task = bot["current_task"]
                    if task:
                        p_id = task["p_id"]
                        
                        if bot["phase"] == "REP_TO_MAIN":
                            bot["phase"] = "LIFT_UP_MAIN"
                            bot["waiting_for_feedback"] = True
                            self._send_lift_command(r_id, "UP")
                            return
                        
                        elif bot["phase"] == "REP_TO_TEMP":
                            bot["phase"] = "LIFT_DOWN_TEMP"
                            bot["waiting_for_feedback"] = True
                            self._send_lift_command(r_id, "DOWN")
                            return
                        
                        elif bot["phase"] == "REP_TO_EXTRA":
                            bot["phase"] = "LIFT_UP_EXTRA"
                            bot["waiting_for_feedback"] = True
                            self._send_lift_command(r_id, "UP")
                            return
                        
                        elif bot["phase"] == "REP_TO_MAIN_2":
                            bot["phase"] = "LIFT_DOWN_MAIN_2"
                            bot["waiting_for_feedback"] = True
                            self._send_lift_command(r_id, "DOWN")
                            return
                            
                        elif bot["phase"] == "REP_TO_TEMP_2":
                            bot["phase"] = "LIFT_UP_TEMP_2"
                            bot["waiting_for_feedback"] = True
                            self._send_lift_command(r_id, "UP")
                            return
                            
                        elif bot["phase"] == "REP_TO_EXTRA_2":
                            bot["phase"] = "LIFT_DOWN_EXTRA_2"
                            bot["waiting_for_feedback"] = True
                            self._send_lift_command(r_id, "DOWN")
                            return
                            
                    if bot["phase"] == "TO_SPAWN" and curr_node == "robot_spawn_5":
                        bot["full_path"] = []
                        bot["phase"] = "IDLE"
                        bot["current_lock"] = [curr_node]
                    
                    bot["current_idx"] = 0
                
                else:
                    if bot["phase"] == "TO_PALETTE":
                        p_node = bot["target_p"]  
                        p_id = int(p_node.split('_')[1])
                        
                        bot["last_node"] = curr_node
                        bot["full_path"] = []  
                        bot["current_idx"] = 0
                        bot["phase"] = "WAITING_FOR_UR10"
                        
                        self.is_reached_pallet[p_id] = 1 
                        self.get_logger().info(f"로봇 {r_id}가 {p_id}번 파레트에 도착. UR10_02에 적재를 지시합니다.")

                        current_stock = self.current_stocks.get(p_id, 4) # 못 찾으면 기본값 4
                        stuff_val = 5 - current_stock # stock이 4면 stuff는 1, 3이면 2...
                        
                        if stuff_val < 1: stuff_val = 1
                        if stuff_val > 4: stuff_val = 4
                        
                        if self.current_stocks[p_id] > 0:
                            self.current_stocks[p_id] -= 1
                            
                        self.get_logger().info(f"로봇 {r_id}가 {p_id}번 파레트 도착. (재고:{current_stock} -> 위치:{stuff_val})")
                        
                        msg_data = {"robot_id": r_id, "pallet_id": p_id, "stuff": stuff_val}
                        self.ur10_pub.publish(String(data=json.dumps(msg_data)))
                    
                    
                    elif bot["phase"] == "TO_USER":
                        bot["last_node"] = curr_node
                        bot["full_path"] = []
                        bot["current_idx"] = 0
                        bot["phase"] = "WAITING_FOR_UR10_USER" 
                        
                        task = bot["current_task"]
                        if task:
                            oid = task["oid"]
                            uid = task["uid"]
                            if oid in self.order_tracker:
                                self.order_tracker[oid]["completed"] += 1
                                curr_stack = self.order_tracker[oid]["completed"]
                                total = self.order_tracker[oid]["total"]
                                
                                is_full = 1 if curr_stack >= total else 0
                                
                                self.get_logger().info(f"로봇 {r_id} 유저 {uid} 도착. (Stack: {curr_stack}/{total}) UR10_01 하차 대기.")
                                
                                if is_full == 1:
                                    self.user_locked[uid] = True
                                    threading.Thread(target=self._update_fb_status, args=(f"Order/{oid}", "배송 접수 중")).start()
                                    event_msg = String(data=json.dumps({"event": "ORDER_COMPLETED", "order_id": oid, "user_id": uid}))
                                    self.order_completed_pub.publish(event_msg)
                                
                                msg_data = {
                                    "robot_id": r_id,
                                    "user_id": uid,
                                    "stack": curr_stack,
                                    "is_full": is_full
                                }
                                self.ur10_user_pub.publish(String(data=json.dumps(msg_data)))
                    
                    elif bot["phase"] == "TO_SPAWN":
                        bot["last_node"] = curr_node
                        bot["full_path"] = []
                        bot["load"] = "None"
                        bot["phase"] = "IDLE"
                        bot["current_lock"] = [curr_node]
                        bot["current_idx"] = 0

            self._broadcast(r_id, bot)

    def _broadcast(self, r_id, bot):
        curr_node = bot["full_path"][bot["current_idx"]] if bot["full_path"] else bot["last_node"]
        is_at_entry_to_spawn = (bot["phase"] == "TO_SPAWN" and curr_node == f"entry_{r_id}")
        
        if not bot["full_path"] or is_at_entry_to_spawn:
            mode = "Rest"
        else:
            mode = "Move"
            
        self.status_pub.publish(String(data=json.dumps({"robot_id": r_id, "mode": mode, "load": bot["load"]})))
        
        if bot["full_path"] and bot["current_idx"] < len(bot["full_path"]):
            segment = bot["full_path"][bot["current_idx"] : bot["current_idx"] + 3]
            coords = [list(self.G.nodes[n]['pos']) for n in segment]
            self.path_pub.publish(String(data=json.dumps({"robot_id": r_id, "path": coords})))

    def _control_loop(self):
        while rclpy.ok():
            with self.lock:
                idle_robots = [r_id for r_id in range(1, 5) if self.robots[r_id]["phase"] == "IDLE" and not self.robots[r_id]["full_path"]]
                while idle_robots:
                    next_task = self._get_next_available_task()
                    if not next_task:
                        break 
                        
                    target_p = next_task["item_id"]
                    best_robot_id = None
                    min_distance = float('inf')
                    for r_id in idle_robots:
                        bot = self.robots[r_id]
                        try:
                            dist = nx.shortest_path_length(self.G, source=bot["last_node"], target=target_p, weight='weight')
                        except nx.NetworkXNoPath:
                            dist = float('inf')
                        if dist < min_distance:
                            min_distance = dist
                            best_robot_id = r_id
                            
                    if best_robot_id is None:
                        best_robot_id = idle_robots[0] 
                    bot = self.robots[best_robot_id]
                    u = f"user_{next_task['uid']}"
                    bot.update({
                        "target_p": target_p, 
                        "target_u": u, 
                        "phase": "TO_PALETTE", 
                        "full_path": self._get_dynamic_path(best_robot_id, bot["last_node"], target_p), 
                        "current_idx": 0,
                        "current_task": next_task
                    })
                    threading.Thread(target=self._update_fb_status, args=(f"Order/{next_task['oid']}", "상품 적재 중")).start()
                    self._broadcast(best_robot_id, bot)
                    idle_robots.remove(best_robot_id)

                bot5 = self.robots[5]
                if bot5["phase"] == "IDLE" and not bot5["full_path"] and self.replenish_queue:
                    rep_task = self.replenish_queue.pop(0)
                    pid = rep_task["p_id"]
                    bot5.update({
                        "target_p": f"palette_{pid:02d}",
                        "phase": "REP_TO_MAIN",
                        "full_path": self._get_dynamic_path(5, bot5["last_node"], f"palette_{pid:02d}"),
                        "current_idx": 0,
                        "current_task": rep_task
                    })
                    self._broadcast(5, bot5)

                for r_id, bot in self.robots.items():
                    if bot["full_path"] and bot["current_idx"] < len(bot["full_path"]) - 1:
                        if bot["waiting_for_feedback"] or len(bot["current_lock"]) >= 2: continue
                        
                        next_node = bot["full_path"][bot["current_idx"] + 1]
                        
                        forbidden = set()
                        for rid_other, b_other in self.robots.items():
                            if rid_other != r_id:
                                forbidden.update(b_other["current_lock"])
                                o_curr = b_other["full_path"][b_other["current_idx"]] if b_other["full_path"] else b_other["last_node"]
                                if o_curr in self.gateways:
                                    forbidden.add(self.gateways[o_curr])
                        
                        if next_node not in forbidden or next_node in bot["current_lock"]:
                            bot["current_lock"].append(next_node)
                            bot["waiting_for_feedback"] = True
                            
                            curr_node = bot["full_path"][bot["current_idx"]]
                            is_reverse = False
                            
                            # ========================================================
                            # [핵심 추가] 1~4번 로봇이 파레트에서 방금 물건을 받고 빠져나올 때 (단 한 칸만) 후진!
                            # ========================================================
                            if r_id in [1, 2, 3, 4]:
                                # 현재 노드가 파레트 픽업 지점(item_xx)인지 확인 (문자열 타입이므로 isinstance 체크)
                                if isinstance(curr_node, str) and curr_node.startswith("item_"):
                                    # 파레트에서 유저에게 출발하는 첫 번째 엣지인 경우에만 후진
                                    if bot["phase"] == "TO_USER" and bot["current_idx"] == 0:
                                        is_reverse = True
                                        
                            if r_id == 5:
                                is_inside_space = isinstance(curr_node, str) and (curr_node.startswith("palette_") or curr_node.startswith("extra_") or curr_node.startswith("temp_"))
                                is_going_out = isinstance(next_node, str) and next_node.startswith("mid_")
                                
                                if is_inside_space and is_going_out:
                                    is_reverse = True
                            
                            msg_data = {
                                "robot_id": r_id, 
                                "next_pos": list(self.G.nodes[next_node]['pos']),
                                "reverse": is_reverse 
                            }
                            
                            self.publisher_.publish(String(data=json.dumps(msg_data)))
                        else:
                            bot["full_path"] = self._get_dynamic_path(r_id, bot["full_path"][bot["current_idx"]], bot["full_path"][-1])
                            bot["current_idx"] = 0
            time.sleep(0.4)

    def _firebase_sync_loop(self):
        while rclpy.ok():
            try:
                ref = db.reference('Order')
                orders = ref.order_by_child('status').equal_to('상품 준비 중').get()
                
                if orders:
                    with self.lock:
                        for oid, order_data in orders.items():
                            if oid not in self.processed_orders:
                                uid = int(order_data.get('ordererId', 1))
                                items = order_data.get('items', [])
                                total_items = 0
                                
                                for idx, item in enumerate(items):
                                    item_id = item.get('id')
                                    qty = item.get('quantity', 1)
                                    total_items += qty
                                    
                                    for q in range(1, qty + 1):
                                        self.task_queue.append({
                                            "oid": oid,
                                            "item_id": item_id,
                                            "item_idx": idx,
                                            "uid": uid,
                                            "total_qty": qty,
                                            "current_qty": q
                                        })
                                        
                                self.order_tracker[oid] = {"total": total_items, "completed": 0, "uid": uid}
                                self.processed_orders.add(oid)
            except Exception as e:
                pass
            time.sleep(2.0)

    def _firebase_replenish_loop(self):
        while rclpy.ok():
            try:
                ref = db.reference('products')
                products = ref.get()
                
                if products:
                    with self.lock:
                        for item_key, item_data in products.items():
                            if not item_key.startswith('item_'): 
                                continue
                            
                            pid = int(item_key.split('_')[1])
                            
                            try:
                                stock = int(item_data.get('stock', 0))
                                waiting_stock = int(item_data.get('Waiting Stock', 0))
                                self.current_stocks[pid] = stock
                            except (ValueError, TypeError):
                                continue
                            
                            if stock == 0 and waiting_stock > 0 and pid not in self.replenishing_items:
                                self.replenish_queue.append({
                                    "p_id": pid,
                                    "item_key": item_key,
                                    "waiting_stock": waiting_stock
                                })
                                self.replenishing_items.add(pid)
            except Exception as e:
                pass
            time.sleep(2.0)

def main():
    rclpy.init()
    rclpy.spin(IWHubMissionController())
    rclpy.shutdown()

if __name__ == '__main__':
    main()