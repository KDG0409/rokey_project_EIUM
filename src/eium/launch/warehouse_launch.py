## warehouse_launch.py

import os
from launch import LaunchDescription
from launch.actions import ExecuteProcess, TimerAction
from launch_ros.actions import Node

def generate_launch_description():
    # 경로 설정
    isaac_python = '/home/rokey/isaacsim/python.sh'
    warehouse_script = '/home/rokey/IsaacSim-ros_workspaces/humble_ws/src/eium/eium/warehouse.py'

    # --- 1. warehouse.py (Isaac Sim) 전용 환경 변수 격리 ---
    env_isaac = os.environ.copy()
    old_python_path = env_isaac.get('PYTHONPATH', '').split(':')
    filtered_path = [p for p in old_python_path if '/opt/ros' not in p] 
    
    isaac_ros2_path = "/home/rokey/isaacsim/exts/isaacsim.ros2.bridge/humble/lib/python3.10/site-packages"
    env_isaac['PYTHONPATH'] = f"{isaac_ros2_path}:" + ":".join(filtered_path)
    env_isaac['RMW_IMPLEMENTATION'] = 'rmw_fastrtps_cpp'

    # --- 2. 프로세스 정의 ---
    
    # 프로세스 A: 시뮬레이션 서버 (가장 먼저 실행됨)
    warehouse_process = ExecuteProcess(
        cmd=[isaac_python, warehouse_script],
        output='screen',
        env=env_isaac
    )

    # 💡 [추가] 프로세스 B: MicroXRCEAgent (PX4 - ROS 2 통역사)
    # 아이작 심과 함께 바로 실행되도록 설정합니다.
    micro_xrce_agent = ExecuteProcess(
        cmd=['MicroXRCEAgent', 'udp4', '-p', '8888'],
        output='screen'
    )

    # 프로세스 C: 임무 제어 노드 및 드론 제어 노드 (시뮬레이터 로딩 대기 후 15초 뒤에 켜짐)
    delayed_controllers = TimerAction(
        period=15.0,
        actions=[
            # 기존 AGV 컨트롤러
            Node(
                package='eium',
                executable='iw_hub_controller', 
                name='iw_hub_mission_controller',
                output='screen'
            ),
            # 💡 [추가] 드론 Offboard 제어 노드
            # 아이작 심과 PX4가 완전히 켜진 15초 뒤에 켜져서 명령을 대기합니다.
            Node(
                package='eium',
                executable='drone_offboard', 
                name='drone_offboard_controller',
                output='screen'
            )
        ]
    )

    # 3. 런치 실행
    return LaunchDescription([
        warehouse_process,
        micro_xrce_agent,      # 통역사 추가
        delayed_controllers    # 지연 실행되는 노드들(AGV + 드론) 추가
    ])