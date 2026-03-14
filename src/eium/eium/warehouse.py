#!/usr/bin/env python3
# warehouse.py

from isaacsim import SimulationApp
import carb
import os
import sys
import time

# 1. 시뮬레이션 앱 초기화
simulation_app = SimulationApp({"headless": False})

carb.settings.get_settings().set("/log/level", "error")
carb.settings.get_settings().set("/physics/suppressJointTransformWarning", True)

# Isaac Sim 5.0 공식 익스텐션 활성화 함수
from isaacsim.core.utils.extensions import enable_extension
# 💡 [핵심] 익스텐션 매니저를 부르기 위한 필수 모듈
import omni.kit.app 

# 기본 익스텐션 활성화
enable_extension("omni.isaac.ros2_bridge")
enable_extension("omni.graph.action") 
enable_extension("omni.isaac.conveyor")

# =====================================================================
# 🛠️ [Isaac Sim 5.0] Pegasus 익스텐션 정식 등록 및 로드
# =====================================================================
# 1. 앱 인터페이스에서 익스텐션 매니저 가져오기
ext_manager = omni.kit.app.get_app().get_extension_manager()

# 2. Pegasus 익스텐션이 있는 폴더 경로 등록
pegasus_ext_path = "/home/rokey/IsaacSim-ros_workspaces/humble_ws/src/PegasusSimulator/extensions"
ext_manager.add_path(pegasus_ext_path)

# 3. Pegasus 익스텐션 즉시 활성화 (내부 pxr 모듈 등 자동 연동)
ext_manager.set_extension_enabled_immediate("pegasus.simulator", True)

# 4. 활성화가 완전히 끝난 후 Pegasus 모듈 임포트
from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface
from pegasus.simulator.logic.vehicles.multirotor import Multirotor
from pegasus.simulator.logic.backends.px4_mavlink_backend import PX4MavlinkBackend, PX4MavlinkBackendConfig
from scipy.spatial.transform import Rotation
# =====================================================================

import json
import numpy as np
import omni.usd
from pxr import UsdGeom, Gf, UsdPhysics

# ROS 2 모듈 임포트
import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32

from isaacsim.core.api import World
from isaacsim.core.utils.stage import add_reference_to_stage
from isaacsim.storage.native import get_assets_root_path
from isaacsim.robot.wheeled_robots.robots import WheeledRobot
from isaacsim.robot.manipulators import SingleManipulator
from isaacsim.robot.manipulators.grippers import SurfaceGripper
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.robot.wheeled_robots.controllers.wheel_base_pose_controller import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers.differential_controller import DifferentialController

# [UR10 추가] RMPFlow 및 회전 유틸리티 임포트
from isaacsim.core.utils.rotations import euler_angles_to_quat
from isaacsim.core.prims import SingleArticulation
import isaacsim.robot_motion.motion_generation as mg

from scipy.spatial.transform import Rotation

# ========================================================
# UR10 RMPFlow 컨트롤러 클래스
# ========================================================
class RMPFlowController(mg.MotionPolicyController):
    def __init__(self, name: str, robot_articulation: SingleArticulation, physics_dt: float = 1.0 / 60.0, attach_gripper: bool = False) -> None:
        if attach_gripper:
            self.rmp_flow_config = mg.interface_config_loader.load_supported_motion_policy_config("UR10", "RMPflowSuction")
        else:
            self.rmp_flow_config = mg.interface_config_loader.load_supported_motion_policy_config("UR10", "RMPflow")
        self.rmp_flow = mg.lula.motion_policies.RmpFlow(**self.rmp_flow_config)
        self.articulation_rmp = mg.ArticulationMotionPolicy(robot_articulation, self.rmp_flow, physics_dt)

        mg.MotionPolicyController.__init__(self, name=name, articulation_motion_policy=self.articulation_rmp)
        (self._default_position, self._default_orientation) = self._articulation_motion_policy._robot_articulation.get_world_pose()
        self._motion_policy.set_robot_base_pose(robot_position=self._default_position, robot_orientation=self._default_orientation)

    def reset(self):
        mg.MotionPolicyController.reset(self)
        self._motion_policy.set_robot_base_pose(robot_position=self._default_position, robot_orientation=self._default_orientation)

# ========================================================
# UR10 두 대의 상태 머신을 관리하는 매니저 클래스 
# ========================================================
class UR10Manager:
    def __init__(self, ur10_1, ur10_2, ctrl1, ctrl2, ros_node):
        self.ur10_1 = ur10_1 # UR_10_01 (오른쪽 - 유저 배송 하차 담당)
        self.ur10_2 = ur10_2 # UR_10_02 (왼쪽 - 파레트 적재 담당)
        self.ctrl1 = ctrl1
        self.ctrl2 = ctrl2
        self.ros_node = ros_node
        
        self.task_phase1 = 1.0
        self.task_phase2 = 1.0
        self._wait_counter1 = 0
        self._wait_counter2 = 0
        
        # 큐 관리를 위한 변수
        self.current_user_task_1 = None  
        self.current_pallet_2 = None
        
        self.palette = 0
        self.stuff = 0
        self.user = 0
        self.stack = 0
        self.full = 0
        self.agv_id = 0

        self.delivery_start_time = None
    def update(self):
        # ----------------------------------------------------
        # 1. UR_10_01 (Task 1) State Machine (유저 배송 하차 담당)
        # ----------------------------------------------------
        if not hasattr(self, 'current_user_task_1'):
            self.current_user_task_1 = None

        if self.task_phase1 == 1.0 and self.current_user_task_1 is None:
            if len(self.ros_node.ur10_user_queue) > 0:
                self.current_user_task_1 = self.ros_node.ur10_user_queue.pop(0)
                self.user = self.current_user_task_1["user_id"]
                self.stack = self.current_user_task_1["stack"]
                self.full = self.current_user_task_1["is_full"]
                self.agv_id = self.current_user_task_1["robot_id"]
                print(f"[UR_10_01] 유저 {self.user}번 하차 시작! (Stack: {self.stack}, Full: {self.full})")

        if self.current_user_task_1 is not None:
            i = self.user
            j = self.stack
            target_rail_pos1 = 15.0 - (6 * i) 
            target_rail_pos2 = 15.0 - (6 * i) - 0.5 
            
            # AGV 위 위치 (박스를 내려놓을 목표점)
            x, y = 8, 23 - 9 - 2
            position = np.array([x, y, 0.325])

            if j == 1:   position2 = np.array([10.1 - 0.35, 23 - 9 - 2 + 0.15 - 0.15, 0.3])
            elif j == 2: position2 = np.array([10.1 - 0.35, 23 - 9 - 2 - 0.15 , 0.3])
            elif j == 3: position2 = np.array([10.1 - 0.35, 23 - 9 - 2 + 0.15 - 0.15, 0.5])
            elif j >= 4: position2 = np.array([10.1 - 0.35, 23 - 9 - 2 - 0.15 - 0.1, 0.5])
            
            position3 = np.array([8 + 0.5, 23 - 9 - 2 + 0.5 + 0.2, 0.82])
            position4 = np.array([8 + 1.2 , 23 - 9 - 2 + 0.5 + 0.2, 0.82])
            position4_5 = np.array([8 + 1.2 , 23 - 9 - 2 + 0.5 , 0.82])

            x2, y2 = 9.5 + 0.18 -0.3 -0.02 , 23 - 9- 2 + 0.15 + 0.3
            position5 = np.array([x2, y2 ,0.382])

            x3, y3  = 8 + 1.1 , 23 -9 -2 + 0.5
            position6 = np.array([x3, y3 ,0.8])

            if self.task_phase1 == 1.0:
                rail_action = ArticulationAction(joint_positions=np.array([target_rail_pos1]), joint_indices=np.array([0]))
                self.ur10_1.apply_action(rail_action)
                if abs(self.ur10_1.get_joint_positions()[0] - target_rail_pos1) < 0.05:
                    self.task_phase1 = 1.5     

            elif self.task_phase1 == 1.5:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos1 = position.copy()
                pos1[2] = 0.818
                action = self.ctrl1.forward(target_end_effector_position=pos1, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 2.0 

            elif self.task_phase1 == 2.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 3.0  

            elif self.task_phase1 == 3.0:
                self.ur10_1.gripper.close()
                self.task_phase1 = 4.0

            elif self.task_phase1 == 4.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos1 = position.copy()
                pos1[2] = 0.818
                action = self.ctrl1.forward(target_end_effector_position=pos1, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 4.5

            elif self.task_phase1 == 4.5:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position3, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 4.8

            elif self.task_phase1 == 4.8:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position4, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, np.pi/2])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 4.9

            elif self.task_phase1 == 4.9:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position4_5, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, np.pi/2])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 5.0

            elif self.task_phase1 == 5.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos2 = position2.copy()
                pos2[2] = 0.818
                action = self.ctrl1.forward(target_end_effector_position=pos2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, np.pi/2])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 6.0

            elif self.task_phase1 == 6.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, np.pi/2])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 7.0

            elif self.task_phase1 == 7.0:
                self.ur10_1.gripper.open()
                if self._wait_counter1 < 30: 
                    self._wait_counter1 += 1
                else:
                    self._wait_counter1 = 0
                    self.task_phase1 = 7.5

            elif self.task_phase1 == 7.5:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos2 = position2.copy()
                pos2[2] = 0.818
                action = self.ctrl1.forward(target_end_effector_position=pos2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 7.8

            elif self.task_phase1 == 7.8:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position4, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, np.pi/2])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 8.0

            elif self.task_phase1 == 8.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl1.forward(target_end_effector_position=position3, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, np.pi/2])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    
                    # [핵심] 주문의 마지막 물품(full == 1)일 경우 컨베이어벨트 적재 트리거!
                    if self.full == 1:
                        print(f" [UR_10_01] 유저 {self.user}번 박스가 가득 찼습니다! (배송 준비 중) 컨베이어로 이동합니다.")
                        self.task_phase1 = 8.2  # 컨베이어 추가 동작 페이즈로 넘어감
                        self.full = 0
                        
                        # [추가] 바로 지금! 이 순간의 시간을 기록합니다.
                        self.delivery_start_time = time.time()
                    else:
                        print(f" [UR_10_01] 유저 {self.user}번 아이템 하차 완료. (남은 주문 대기 중)")
                        self.task_phase1 = 1.0
                        self.ros_node.send_ur10_user_done(self.agv_id) 
                        self.current_user_task_1 = None 

            # ===============================================================
            # [추가] 컨베이어 벨트로 상자를 옮기는 추가 동작 (Phase 9.0 ~ 13.0)
            # ===============================================================
            elif self.task_phase1 == 8.2:
                rail_action = ArticulationAction(joint_positions=np.array([target_rail_pos2]), joint_indices=np.array([0]))
                self.ur10_1.apply_action(rail_action)
                if abs(self.ur10_1.get_joint_positions()[0] - target_rail_pos1) < 0.05:
                    self.task_phase1 = 8.5

            elif self.task_phase1 == 8.5:
                # 다시 AGV 위 박스 위치로 접근
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                action = self.ctrl1.forward(target_end_effector_position=position6, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 9.0

            elif self.task_phase1 == 9.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                action = self.ctrl1.forward(target_end_effector_position=position5, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 10.0

            elif self.task_phase1 == 10.0:
                # 상자 잡기
                self.ur10_1.gripper.close()
                if self._wait_counter1 < 30: 
                    self._wait_counter1 += 1
                else:
                    self._wait_counter1 = 0
                    self.task_phase1 = 11.0

            elif self.task_phase1 == 11.0:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                pos1 = position5.copy()
                pos1[2] = 0.8

                action = self.ctrl1.forward(target_end_effector_position=pos1, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 11.2

            elif self.task_phase1 == 11.2:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                pos2 = position5.copy()
                pos2[0] += 0.85
                pos2[2] = 0.8

                action = self.ctrl1.forward(target_end_effector_position=pos2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 11.5

            elif self.task_phase1 == 11.5:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                pos3 = position5.copy()
                pos3[0] += 0.85
                pos3[2] = 0.7

                action = self.ctrl1.forward(target_end_effector_position=pos3, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 12.0

            elif self.task_phase1 == 12.0:
                # 컨베이어 위에 내려놓기
                self.ur10_1.gripper.open()
                if self._wait_counter1 < 30: 
                    self._wait_counter1 += 1
                else:
                    self._wait_counter1 = 0
                    self.task_phase1 = 12.5

            elif self.task_phase1 == 12.5:
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                pos4 = position5.copy()
                pos4[0] += 0.85
                pos4[2] = 0.8

                action = self.ctrl1.forward(target_end_effector_position=pos4, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 12.8

            elif self.task_phase1 == 12.8:
                # 다시 AGV 위 박스 위치로 접근
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                pos5 = position5.copy()
                pos5[2] = 0.8

                action = self.ctrl1.forward(target_end_effector_position=pos5, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 13

            elif self.task_phase1 == 13:
                # 다시 AGV 위 박스 위치로 접근
                pos, ori = self.ur10_1.get_world_pose()
                self.ctrl1._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)

                action = self.ctrl1.forward(target_end_effector_position=position6, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_1.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_1.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl1.reset()
                    self.task_phase1 = 1.0
                    print(f"🏭 [UR_10_01] 유저 {self.user}번 박스 컨베이어 안착 완료! 최종 출발 신호 발송.")
                    self.ros_node.send_ur10_user_done(self.agv_id) 
                    self.current_user_task_1 = None 


        # ----------------------------------------------------
        # 2. UR_10_02 (Task 2) State Machine (파레트 구역 담당)
        # ----------------------------------------------------
        if not hasattr(self, 'current_pallet_2'):
            self.current_pallet_2 = None

        if self.task_phase2 == 1.0 and self.current_pallet_2 is None:
            if len(self.ros_node.ur10_queue) > 0:
                task_info = self.ros_node.ur10_queue.pop(0)
                self.current_pallet_2 = task_info["pallet_id"]
                self.palette = self.current_pallet_2
                self.stuff = task_info["stuff"]
                print(f"[UR_10_02] 큐 확인! 파레트 {self.current_pallet_2}번에서 박스 적재를 시작합니다.")

        if self.current_pallet_2 is not None:
            i = self.palette
            j = self.stuff
            target_rail_pos2 = 13.0 - (2 * i)   

            if j == 1:
                position = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4, 26.6 - 0.44256 - 2 - 12 + 0.44256/4, 0.584])
                position2 = np.array([-20 + 0.42242 + 9 + 1 + 1.5 + 0.3  , 23 - 9 - 2 -0.07 , 0.335])
                position3 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 0.5, 26.6 - 0.44256 - 2 - 12 + 0.44256/4, 0.8])
                position4 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 1.0, 26.6 - 0.44256 - 2 - 12 + 0.44256/4 + 0.2, 0.8])
            elif j == 2:
                position = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4, 26.6 - 0.44256 - 2 - 12 + 0.44256/4 - 0.5, 0.584])
                position2 = np.array([-20 + 0.42242 + 9 + 1 + 1.5 + 0.3, 23 - 9 - 2-0.07, 0.335])
                position3 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 0.5, 26.6 - 0.44256 - 2 - 12 + 0.44256/4, 0.8])
                position4 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 1.0, 26.6 - 0.44256 - 2 - 12 + 0.44256/4 + 0.2, 0.8])
            elif j == 3:
                position = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 - 0.5, 26.6 - 0.44256 - 2 - 12 + 0.44256/4, 0.584])
                position2 = np.array([-20 + 0.42242 + 9 + 1 + 1.5 + 0.3, 23 - 9 - 2 - 0.07, 0.335])
                position3 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 0.5, 26.6 - 0.44256 - 2 - 12 + 0.44256/4, 0.8])
                position4 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 1.0, 26.6 - 0.44256 - 2 - 12 + 0.44256/4 + 0.2, 0.8])
            elif j == 4:
                position = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 - 0.5, 26.6 - 0.44256 - 2 - 12 + 0.44256/4 - 0.5, 0.584])
                position2 = np.array([-20 + 0.42242 + 9 + 1 + 1.5 + 0.3, 23 - 9 - 2 - 0.07, 0.335])
                position3 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 0.5, 26.6 - 0.44256 - 2 - 12 + 0.44256/4, 0.8])
                position4 = np.array([-20 + 0.42242 + 9 + 1 - 0.42242/4 + 1.0, 26.6 - 0.44256 - 2 - 12 + 0.44256/4 + 0.2, 0.8])

            if self.task_phase2 == 1.0:
                rail_action = ArticulationAction(joint_positions=np.array([target_rail_pos2]), joint_indices=np.array([0]))
                self.ur10_2.apply_action(rail_action)
                if abs(self.ur10_2.get_joint_positions()[0] - target_rail_pos2) < 0.05: 
                    self.task_phase2 = 1.5     

            elif self.task_phase2 == 1.5:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos1 = position.copy()
                pos1[2] = 0.8
                action = self.ctrl2.forward(target_end_effector_position=pos1, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 2.0 

            elif self.task_phase2 == 2.0:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl2.forward(target_end_effector_position=position, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 3.0    

            elif self.task_phase2 == 3.0:
                self.ur10_2.gripper.close()
                self.task_phase2 = 4.0

            elif self.task_phase2 == 4.0:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos1 = position.copy()
                pos1[2] = 0.8
                action = self.ctrl2.forward(target_end_effector_position=pos1, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 4.5

            elif self.task_phase2 == 4.5:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl2.forward(target_end_effector_position=position3, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 4.8

            elif self.task_phase2 == 4.8:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl2.forward(target_end_effector_position=position4, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 5.0

            elif self.task_phase2 == 5.0:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos2 = position2.copy()
                pos2[2] = 0.9
                action = self.ctrl2.forward(target_end_effector_position=pos2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 6.0

            elif self.task_phase2 == 6.0:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl2.forward(target_end_effector_position=position2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 7.0

            elif self.task_phase2 == 7.0:
                self.ur10_2.gripper.open()
                if self._wait_counter2 < 30: 
                    self._wait_counter2 += 1
                else:
                    self._wait_counter2 = 0
                    self.task_phase2 = 7.5

            elif self.task_phase2 == 7.5:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                pos2 = position2.copy()
                pos2[2] = 0.9
                action = self.ctrl2.forward(target_end_effector_position=pos2, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 7.7

            elif self.task_phase2 == 7.7:
                pos, ori = self.ur10_2.get_world_pose()
                self.ctrl2._motion_policy.set_robot_base_pose(robot_position=pos, robot_orientation=ori)
                action = self.ctrl2.forward(target_end_effector_position=position4, target_end_effector_orientation=euler_angles_to_quat(np.array([0, np.pi/2, 0])))
                self.ur10_2.apply_action(ArticulationAction(joint_positions=action.joint_positions, joint_indices=np.array([1, 2, 3, 4, 5, 6])))
                if np.all(np.abs(self.ur10_2.get_joint_positions()[1:7] - action.joint_positions) < 0.001):
                    self.ctrl2.reset()
                    self.task_phase2 = 1

                    print(f" [UR_10_02] 파레트 {self.current_pallet_2}번 박스 AGV 적재 완료!")
                    self.ros_node.send_ur10_done(self.current_pallet_2)
                    self.current_pallet_2 = None 


class WarehouseCommunicator(Node):
    def __init__(self):
        super().__init__('warehouse_communicator')
        self.mission_sub = self.create_subscription(String, '/robot_missions', self.mission_callback, 10)
        self.feedback_pub = self.create_publisher(String, '/robot_feedback', 10)
        
        self.target_pos = {1: None, 2: None, 3: None, 4: None, 5: None}
        self.is_moving = {1: False, 2: False, 3: False, 4: False, 5: False}
        self.is_reverse = {1: False, 2: False, 3: False, 4: False, 5: False}
        
        self.lift_action = {1: None, 2: None, 3: None, 4: None, 5: None}
        self.lift_start_time = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
        self.lift_state = {1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.00} 

        self.ur10_queue = []
        self.ur10_task_sub = self.create_subscription(String, '/ur10_task_queue', self.ur10_task_callback, 10)
        self.ur10_done_pub = self.create_publisher(String, '/ur10_task_done', 10)

        # 💡 [핵심 추가] 유저 배송 하차 전용 큐 및 통신
        self.ur10_user_queue = []
        self.ur10_user_task_sub = self.create_subscription(String, '/ur10_user_queue', self.ur10_user_task_callback, 10)
        self.ur10_user_done_pub = self.create_publisher(String, '/ur10_user_done', 10)

        self.takeoff_pub = self.create_publisher(Int32, '/iris_takeoff', 10)

    def send_takeoff_signal(self):
        msg = Int32()
        msg.data = 1
        self.takeoff_pub.publish(msg)

    def ur10_task_callback(self, msg):
        try:
            data = json.loads(msg.data)
            p_id = data.get("pallet_id")
            stuff_val = data.get("stuff", 1)
            if p_id is not None:
                self.ur10_queue.append({"pallet_id": p_id, "stuff": stuff_val})
                self.get_logger().info(f" UR10_02 큐 추가: 파레트 {p_id}번 (위치: {stuff_val}) 적재 대기 중")
        except Exception as e: 
            pass

    def send_ur10_done(self, pallet_id):
        msg = String(data=json.dumps({"pallet_id": pallet_id}))
        self.ur10_done_pub.publish(msg)

    def ur10_user_task_callback(self, msg):
        try:
            data = json.loads(msg.data)
            self.ur10_user_queue.append(data)
            self.get_logger().info(f"UR10_01 작업 큐 추가: 유저 {data['user_id']}번 배송 하차 대기 중")
        except Exception as e: 
            pass

    def send_ur10_user_done(self, robot_id):
        msg = String(data=json.dumps({"robot_id": robot_id}))
        self.ur10_user_done_pub.publish(msg)

    def mission_callback(self, msg):
        try:
            data = json.loads(msg.data)
            robot_id = data.get("robot_id")
            if robot_id in [1, 2, 3, 4, 5]:
                if "next_pos" in data:
                    self.target_pos[robot_id] = np.array(data.get("next_pos"))
                    self.is_moving[robot_id] = True
                    self.is_reverse[robot_id] = data.get("reverse", False)
                elif "lift" in data:
                    action_type = data.get("lift")
                    self.lift_action[robot_id] = action_type
                    self.lift_state[robot_id] = 0.04 if action_type == "UP" else 0.00
                    self.lift_start_time[robot_id] = time.time()
        except Exception as e: 
            pass

    def send_feedback(self, robot_id):
        msg = String(data=json.dumps({"robot_id": robot_id, "status": "STEP_DONE"}))
        self.feedback_pub.publish(msg)

def main():
    rclpy.init()
    ros_node = WarehouseCommunicator()
    world = World(stage_units_in_meters=1.0)
    
    assets_root_path = get_assets_root_path()
    warehouse_path = "/home/rokey/assets/warehouse.usd" 
    iw_hub_asset = assets_root_path + "/Isaac/Robots/Idealworks/iwhub/iw_hub.usd"
    
    add_reference_to_stage(usd_path=warehouse_path, prim_path="/World/Warehouse")

    robot_names = ["iw_hub_01", "iw_hub_02", "iw_hub_03", "iw_hub_04", "iw_hub_05"]
    spawn_positions = [
        np.array([-6.0, 0.0, 0.1]), np.array([-3.0, 0.0, 0.1]),
        np.array([-6.0, 24.0, 0.1]), np.array([-3.0, 24.0, 0.1]),
        np.array([-12.0, 0.0, 0.1])
    ]

    agvs = []
    controllers = []
    lift_indices = []
    
    for i in range(5):
        agv = world.scene.add(WheeledRobot(
            prim_path=f"/World/{robot_names[i]}", name=robot_names[i], position=spawn_positions[i],
            wheel_dof_names=["left_wheel_joint", "right_wheel_joint"],
            create_robot=True, usd_path=iw_hub_asset
        ))
        agvs.append(agv)
        controllers.append(WheelBasePoseController(
            name=f"ctrl_{i}", 
            open_loop_wheel_controller=DifferentialController(name=f"d_{i}", wheel_radius=0.08, wheel_base=0.5796,max_linear_speed=100.0,max_angular_speed=0.2), 
            is_holonomic=False
        ))
    stage = omni.usd.get_context().get_stage()
    onjoint_usd_path = "/home/rokey/assets/UR_10.usd" 

    print("[INFO] Spawning UR_10_01...")
    add_reference_to_stage(usd_path=onjoint_usd_path, prim_path="/World/UR_10_01_prim")
    UsdGeom.XformCommonAPI(stage.GetPrimAtPath("/World/UR_10_01_prim")).SetTranslate(Gf.Vec3d(9.0, 12.0, 0.0))
    stage.GetPrimAtPath("/World/UR_10_01_prim").GetVariantSet("Gripper").SetVariantSelection("Short_Suction")
    gripper1 = SurfaceGripper(end_effector_prim_path="/World/UR_10_01_prim/UR10_onjoint/ur10/ee_link", surface_gripper_path="/World/UR_10_01_prim/UR10_onjoint/ur10/ee_link/SurfaceGripper")
    ur10_01 = world.scene.add(SingleManipulator(prim_path="/World/UR_10_01_prim/UR10_onjoint", name="UR_10_01", end_effector_prim_path="/World/UR_10_01_prim/UR10_onjoint/ur10/ee_link", gripper=gripper1))
    ur10_01.set_joints_default_state(positions=np.array([-12.0, -np.pi / 2, -np.pi / 2, -np.pi / 2, -np.pi / 2, np.pi / 2, 0]))

    print("[INFO] Spawning UR_10_02...")
    add_reference_to_stage(usd_path=onjoint_usd_path, prim_path="/World/UR_10_02_prim")
    UsdGeom.XformCommonAPI(stage.GetPrimAtPath("/World/UR_10_02_prim")).SetTranslate(Gf.Vec3d(-9.0, 12.0, 0.0))
    stage.GetPrimAtPath("/World/UR_10_02_prim").GetVariantSet("Gripper").SetVariantSelection("Short_Suction")
    gripper2 = SurfaceGripper(end_effector_prim_path="/World/UR_10_02_prim/UR10_onjoint/ur10/ee_link", surface_gripper_path="/World/UR_10_02_prim/UR10_onjoint/ur10/ee_link/SurfaceGripper")
    ur10_02 = world.scene.add(SingleManipulator(prim_path="/World/UR_10_02_prim/UR10_onjoint", name="UR_10_02", end_effector_prim_path="/World/UR_10_02_prim/UR10_onjoint/ur10/ee_link", gripper=gripper2))
    ur10_02.set_joints_default_state(positions=np.array([12.0, -np.pi / 2, -np.pi / 2, -np.pi / 2, -np.pi / 2, np.pi / 2, 0]))

    # =====================================================================
    # Pegasus API를 활용한 Iris 드론 스폰 및 PX4 MAVLink 연동
    # =====================================================================
    print("[INFO] Spawning Iris Drone with PX4 MAVLink backend...")
    
    # 1. Pegasus 인터페이스 초기화 및 World 주입
    pegasus_sim = PegasusInterface()
    pegasus_sim._world = world

    # 2. MAVLink 통신 백엔드 설정
    config = PX4MavlinkBackendConfig({
        "vehicle_id": 0,
        "px4_autostart": 4001
    })
    mavlink_backend = PX4MavlinkBackend(config)
    
    # 3. 드론 객체 생성 (물리 속성 및 백엔드 부착)
    drone_usd_path = "/home/rokey/IsaacSim-ros_workspaces/humble_ws/src/PegasusSimulator/extensions/pegasus.simulator/pegasus/simulator/assets/Robots/Iris/iris.usd"
    # drone_usd_path = "/home/rokey/IsaacSim-ros_workspaces/humble_ws/src/PegasusSimulator/extensions/pegasus.simulator/pegasus/simulator/assets/Robots/Iris/iris_with_box.usd"

    if os.path.exists(drone_usd_path):
        #  [핵심] 최신 Pegasus 표준에 맞춰 Config 객체에 백엔드를 담습니다.
        from pegasus.simulator.logic.vehicles.multirotor import MultirotorConfig
        
        vehicle_config = MultirotorConfig()
        vehicle_config.backends = [mavlink_backend]
        
        #  드론을 생성할 때 config 봇따리를 통째로 넘겨줍니다.
        iris_drone = Multirotor(
            "/World/iris_drone", 
            drone_usd_path, 
            init_pos=[9.0, -1.5, 0.5],
            config=vehicle_config
        )

        # 4. 시뮬레이션에 드론 등록
        # pegasus_sim.get_vehicle(iris_drone)
        
        # 5. 땅에 박히지 않게 드론 위치 1m 띄우기
        # UsdGeom.XformCommonAPI(stage.GetPrimAtPath("/World/iris_drone")).SetTranslate(Gf.Vec3d(9.0, -1.5, 1.0))

        print(" [SUCCESS] Iris Drone and PX4 MAVLink backend loaded successfully!")
    else:
        print(f" [ERROR] Cannot find drone USD file at: {drone_usd_path}")
    # =====================================================================

    world.reset()

    for robot_name in robot_names:
        left_wheel = stage.GetPrimAtPath(f"/World/{robot_name}/left_wheel_joint")
        right_wheel = stage.GetPrimAtPath(f"/World/{robot_name}/right_wheel_joint")
        
        for wheel in [left_wheel, right_wheel]:
            if wheel.IsValid():
                # 힘(maxForce)과 저항(damping) 설정
                if wheel.GetAttribute("drive:angular:physics:maxForce"):
                    wheel.GetAttribute("drive:angular:physics:maxForce").Set(10000000.0)
                if wheel.GetAttribute("drive:angular:physics:damping"):
                    wheel.GetAttribute("drive:angular:physics:damping").Set(10000.0)
                
                # 에러를 발생시켰던 조인트 최대 속도 해제 (정확한 USD 속성명 사용)
                if wheel.GetAttribute("physxJoint:maxJointVelocity"):
                    wheel.GetAttribute("physxJoint:maxJointVelocity").Set(1000.0)
                elif wheel.GetAttribute("drive:angular:physics:maxVelocity"):
                    wheel.GetAttribute("drive:angular:physics:maxVelocity").Set(1000.0)

    ur10_01_ctrl = RMPFlowController(name="ur10_01_ctrl", robot_articulation=ur10_01, attach_gripper=True, physics_dt=1.0 / 30.0)
    ur10_02_ctrl = RMPFlowController(name="ur10_02_ctrl", robot_articulation=ur10_02, attach_gripper=True, physics_dt=1.0 / 30.0)
    
    high_kps = np.array([10000000.0] * 7)
    high_kds = np.array([100000.0] * 7)
    
    ur10_01.get_articulation_controller().set_gains(kps=high_kps, kds=high_kds)
    ur10_02.get_articulation_controller().set_gains(kps=high_kps, kds=high_kds)
    
    nice_posture = np.array([0.0, -1.57, -1.57, -1.57, -1.57, 1.57, 0.0])
    ur10_01_ctrl.rmp_flow.set_cspace_target(nice_posture)
    ur10_02_ctrl.rmp_flow.set_cspace_target(nice_posture)
    
    ur10_manager = UR10Manager(ur10_01, ur10_02, ur10_01_ctrl, ur10_02_ctrl, ros_node)
    
    for agv in agvs: 
        lift_indices.append(agv.get_dof_index("lift_joint"))

    print(" 시뮬레이션 루프 진입")

    sim_start_time = time.time()
    takeoff_signal_sent = False

    while simulation_app.is_running():
        rclpy.spin_once(ros_node, timeout_sec=0.0)
        ur10_manager.update()

        # 마지막 상자 이동 시작 시점부터 20초 대기 후 이륙
        if not takeoff_signal_sent and ur10_manager.delivery_start_time is not None:
            # 기록된 시간으로부터 20초가 지났는지 매 프레임 확인
            if (time.time() - ur10_manager.delivery_start_time) > 60.0:
                ros_node.send_takeoff_signal()
                print(" [Warehouse] 배송 준비! 드론 이륙 토픽(/iris_takeoff = 1) 발송 완료!")
                takeoff_signal_sent = True

        for robot_id in [1, 2, 3, 4, 5]:
            idx = robot_id - 1 
            robot = agvs[idx]
            ctrl = controllers[idx]
            lift_idx = lift_indices[idx]
            
            robot.set_joint_positions(
                positions=np.array([ros_node.lift_state[robot_id]]), 
                joint_indices=np.array([lift_idx])
            )

            if ros_node.lift_action[robot_id] is not None:
                robot.apply_action(ArticulationAction(joint_velocities=np.array([0.0, 0.0]), joint_indices=robot.wheel_dof_indices))
                if time.time() - ros_node.lift_start_time[robot_id] > 1.5:
                    ros_node.lift_action[robot_id] = None 
                    ros_node.send_feedback(robot_id=robot_id)
                continue 

            if ros_node.is_moving[robot_id] and ros_node.target_pos[robot_id] is not None:
                pos, ori = robot.get_world_pose()
                goal = ros_node.target_pos[robot_id][:2] 
                dist_to_goal = np.linalg.norm(pos[:2] - goal)
                
                if ros_node.is_reverse[robot_id]:
                    w, x, y, z = ori[0], ori[1], ori[2], ori[3]
                    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
                    
                    dx = goal[0] - pos[0]
                    dy = goal[1] - pos[1]
                    
                    cos2t = np.cos(2 * yaw)
                    sin2t = np.sin(2 * yaw)
                    
                    dx_prime = -dx * cos2t - dy * sin2t
                    dy_prime = -dx * sin2t + dy * cos2t
                    
                    fake_goal = np.array([pos[0] + dx_prime, pos[1] + dy_prime])
                    action = ctrl.forward(start_position=pos, start_orientation=ori, goal_position=fake_goal)
                    action.joint_velocities = -action.joint_velocities
                else:
                    action = ctrl.forward(start_position=pos, start_orientation=ori, goal_position=goal)

                v_left = action.joint_velocities[0]
                v_right = action.joint_velocities[1]
                
                linear_vel = (v_left + v_right) / 2.0
                angular_vel = (v_right - v_left) / 2.0
                

                boosted_linear = linear_vel * 3.0

                
                final_v_left = boosted_linear - angular_vel
                final_v_right = boosted_linear + angular_vel
                robot.apply_action(ArticulationAction(
                    joint_velocities=np.array([final_v_left, final_v_right]),
                    joint_indices=robot.wheel_dof_indices
                ))

                if np.linalg.norm(pos[:2] - goal) < 0.2:
                    robot.apply_action(ArticulationAction(
                        joint_velocities=np.array([0.0, 0.0]), 
                        joint_indices=robot.wheel_dof_indices
                    ))
                    ctrl.reset()
                    
                    ros_node.is_moving[robot_id] = False
                    ros_node.is_reverse[robot_id] = False
                    ros_node.send_feedback(robot_id=robot_id)

        world.step(render=True)

    ros_node.destroy_node()
    rclpy.shutdown()
    simulation_app.close()

if __name__ == '__main__':
    main()