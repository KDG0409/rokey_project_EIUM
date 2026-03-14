#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleCommand
from std_msgs.msg import Int32

class DroneOffboardController(Node):
    def __init__(self):
        super().__init__('drone_offboard_controller')

        # PX4와 통신하기 위한 필수 QoS 설정 (매우 중요!)
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # 퍼블리셔(명령을 내리는 역할) 생성
        self.offboard_control_mode_publisher = self.create_publisher(OffboardControlMode, '/fmu/in/offboard_control_mode', qos_profile)
        self.trajectory_setpoint_publisher = self.create_publisher(TrajectorySetpoint, '/fmu/in/trajectory_setpoint', qos_profile)
        self.vehicle_command_publisher = self.create_publisher(VehicleCommand, '/fmu/in/vehicle_command', qos_profile)

        self.takeoff_sub = self.create_subscription(Int32, '/iris_takeoff', self.takeoff_callback, 10)

        self.offboard_setpoint_counter = 0
        self.should_takeoff = False      # 이륙 대기 상태
        self.takeoff_triggered = False   # 이륙 명령 중복 실행 방지

        self.timer = self.create_timer(0.1, self.timer_callback) # 10Hz(초당 10번)로 실행

    def takeoff_callback(self, msg):
        if msg.data == 1 and not self.should_takeoff:
            self.get_logger().info("📥 이륙 신호 수신! 이륙 시퀀스 돌입.")
            self.should_takeoff = True
            self.offboard_setpoint_counter = 0

    def publish_vehicle_command(self, command, **params):
        msg = VehicleCommand()
        msg.command = command
        msg.param1 = params.get("param1", 0.0)
        msg.param2 = params.get("param2", 0.0)
        msg.target_system = 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        msg.timestamp = int(self.get_clock().now().nanoseconds / 1000)
        self.vehicle_command_publisher.publish(msg)

    def timer_callback(self):
        # 1. Offboard 하트비트 전송 (PX4는 이 신호가 계속 들어와야 외부 제어를 허락합니다)
        offboard_msg = OffboardControlMode()
        offboard_msg.position = True
        offboard_msg.velocity = False
        offboard_msg.acceleration = False
        offboard_msg.attitude = False
        offboard_msg.body_rate = False
        offboard_msg.timestamp = int(self.get_clock().now().nanoseconds / 1000)
        self.offboard_control_mode_publisher.publish(offboard_msg)

        # 2. 목표 위치 설정 (고도 2.5m로 이륙. NED 좌표계라 Z축이 아래를 향하므로 -2.5가 위쪽입니다)
        trajectory_msg = TrajectorySetpoint()
        # position = [X, Y, Z]
        # World -X 방향으로 20m 이동, World +Z(위) 방향으로 3m 고도 유지
        trajectory_msg.position = [0.0, -30.0, -20.0] 
        
        # 드론의 앞머리(기수)를 날아가는 방향(-X 방향)으로 돌리기
        # 0 = +X 방향(North), 3.14(180도) = -X 방향(South)
        # trajectory_msg.yaw = 3.14
        # trajectory_msg.position = [0.0, 0.0, -2.5] 
        trajectory_msg.yaw = 0.0 # 90도 회전
        trajectory_msg.timestamp = int(self.get_clock().now().nanoseconds / 1000)
        self.trajectory_setpoint_publisher.publish(trajectory_msg)

        # 3. 안전을 위해 처음 10번은 신호만 보내고, 그 다음 시동(Arm)과 Offboard 모드 전환
        if self.should_takeoff:
            if self.offboard_setpoint_counter % 20 == 0:
                self.publish_vehicle_command(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, param1=1.0, param2=6.0)
                self.publish_vehicle_command(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, param1=1.0)
                self.get_logger().info("🔥 PX4에 이륙(Arm & Offboard) 지속 요청 중...")

            self.offboard_setpoint_counter += 1

        # if self.offboard_setpoint_counter < 100:
        #     self.offboard_setpoint_counter += 1

def main(args=None):
    rclpy.init(args=args)
    drone_controller = DroneOffboardController()
    rclpy.spin(drone_controller)
    drone_controller.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
