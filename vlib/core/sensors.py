import carla
import random
import time
import math
import logging
import weakref
from datetime import datetime
from typing import List, Dict, Optional, Any, Callable

logger = logging.getLogger(__name__)

# Global registry for V2X sensors
v2x_sensors = []


class CAMData:
    """Generic Cooperative Awareness Message (CAM) data structure"""
    def __init__(self, sender_id: str, timestamp: datetime, vehicle_data: Optional[Dict] = None, 
                 extensions: Optional[Dict] = None, station_type_override: Optional[str] = None,
                 include_vehicle_data_container: bool = False):
        self.sender_id = sender_id
        self.timestamp = timestamp
        self.vehicle_data = vehicle_data or {}
        self.extensions = extensions or {}  # Generic extensions for different use cases

        # ETSI CAM standard fields
        self.station_id = sender_id
        self.generation_delta_time = 0

        # Basic container
        self.station_type = station_type_override if station_type_override is not None else ("passenger-car" if vehicle_data else "road-side-unit")
        self.include_vehicle_data_container = include_vehicle_data_container
        vd = vehicle_data or {}
        self.position = vd.get("position")
        # self.confidence = vd.get("confidence", 0.95)

        # High frequency container
        self.heading = vd.get("heading", 0.0)
        self.speed = vd.get("speed", 0.0)
        self.acceleration = vd.get("acceleration")
        self.yaw_rate = vd.get("yaw_rate", 0.0)

        # Low frequency container
        self.vehicle_role = vd.get("vehicle_role", "default")
        self.path_history = vd.get("path_history", [])

    def get_extension(self, key: str, default: Any = None) -> Any:
        """Get a value from the extensions dictionary"""
        return self.extensions.get(key, default)

    def set_extension(self, key: str, value: Any) -> None:
        """Set a value in the extensions dictionary"""
        self.extensions[key] = value

    def __str__(self):
        ext_str = f", Extensions: {list(self.extensions.keys())}" if self.extensions else ""
        if self.station_type == "passenger-car":
            return (f"CAM from {self.sender_id} - Position: {self.position}, "
                   f"Speed: {self.speed:.2f} m/s, Heading: {self.heading:.2f}°{ext_str}")
        else:
            return f"CAM from {self.sender_id} (RSU) - Position: {self.position}{ext_str}"
    
    def to_dict(self) -> Dict:
        """Convert CAMData to dictionary for JSON serialization"""
        position_dict = None
        if self.position:
            if isinstance(self.position, dict):
                position_dict = self.position
            elif hasattr(self.position, 'x'):
                position_dict = {
                    "x": self.position.x,
                    "y": self.position.y, 
                    "z": self.position.z
                }
        
        acceleration_dict = None
        if self.acceleration:
            if isinstance(self.acceleration, dict):
                acceleration_dict = self.acceleration
            elif hasattr(self.acceleration, 'x'):
                acceleration_dict = {
                    "x": self.acceleration.x,
                    "y": self.acceleration.y,
                    "z": self.acceleration.z
                }
        
        result = {
            "sender_id": self.sender_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "extensions": self.extensions,
            "station_id": self.station_id,
            "generation_delta_time": self.generation_delta_time,
            "station_type": self.station_type,
            "position": position_dict,
            "heading": self.heading,
            "speed": self.speed,
            "acceleration": acceleration_dict,
            "yaw_rate": self.yaw_rate,
            "vehicle_role": self.vehicle_role,
            "path_history": self.path_history
        }
        if getattr(self, "include_vehicle_data_container", False):
            result["vehicle_data"] = self.vehicle_data
        return result
    
    @classmethod
    def from_dict(cls, data) -> 'CAMData':
        """Create CAMData from dictionary (for JSON deserialization)"""
        from datetime import datetime
        import carla
        
        timestamp = datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now()
        
        position = None
        if data.get("position"):
            pos_data = data["position"]
            position = carla.Location(pos_data["x"], pos_data["y"], pos_data["z"])
        
        acceleration = None
        if data.get("acceleration"):
            accel_data = data["acceleration"]
            acceleration = carla.Vector3D(accel_data["x"], accel_data["y"], accel_data["z"])
        vehicle_data = data.get("vehicle_data", {}).copy()
        if position:
            vehicle_data["position"] = position
        if acceleration:
            vehicle_data["acceleration"] = acceleration
        if data.get("heading") is not None:
            vehicle_data["heading"] = data["heading"]
        if data.get("speed") is not None:
            vehicle_data["speed"] = data["speed"]
        if data.get("yaw_rate") is not None:
            vehicle_data["yaw_rate"] = data["yaw_rate"]
        if data.get("vehicle_role"):
            vehicle_data["vehicle_role"] = data["vehicle_role"]
        if data.get("path_history"):
            vehicle_data["path_history"] = data["path_history"]
            
        cam_data = cls(
            sender_id=data["sender_id"],
            timestamp=timestamp,
            vehicle_data=vehicle_data,
            extensions=data.get("extensions", {})
        )
        
        return cam_data


class V2XSensorConfig:
    """Configuration class for V2X sensor parameters"""
    def __init__(self):
        # RF parameters
        self.transmit_power = 21.5  # dBm
        self.receiver_sensitivity = -99  # dBm
        self.frequency = 5.9  # GHz
        self.filter_distance = 500  # meters

        # CAM generation parameters
        self.gen_cam_min = 0.1  # seconds
        self.gen_cam_max = 1.0  # seconds
        self.low_freq_interval = 0.5  # seconds
        
        # Triggering conditions
        self.position_threshold = 4.0  # meters
        self.heading_threshold = 4.0   # degrees
        self.speed_threshold = 5.0     # m/s
        
        # Advanced features
        self.enable_debug_visualization = False 
        self.max_message_history = 100
        self.communication_timeout = 2.0  # seconds
        self.include_vehicle_data_container = False


class V2XSensor:
    """Generic V2X sensor implementation following ETSI CAM standard"""
    
    def __init__(self, world: carla.World, attach_to: Optional[carla.Actor] = None, 
                 sensor_id: Optional[str] = None, transform: Optional[carla.Transform] = None,
                 config: Optional[V2XSensorConfig] = None):
        self.world = world
        self.sensor_id = sensor_id or f"v2x_{random.randint(1000, 9999)}"
        self.attach_to = attach_to
        self.transform = transform or carla.Transform()
        self.config = config or V2XSensorConfig()

        # Sensor state
        self.location = None
        self.previous_location = None
        self.previous_heading = None
        self.previous_speed = 0.0
        self.received_messages = []
        self.last_cam_time = time.time()
        self.last_low_freq_time = time.time()
        self._is_destroyed = False

        # Initialize dynamic kinematic attributes to satisfy type checkers
        self.heading = 0.0
        self.speed = 0.0
        self.acceleration = None
        self.yaw_rate = 0.0

        self.message_handlers: list[Callable[[CAMData], None]] = []
        self.message_filters: list[Callable[[CAMData], bool]] = []
        self.extensions_provider: Optional[Callable[[], Dict]] = None

        self.gnss_sensor = None
        self.imu_sensor = None
        self.debug_sphere = None

        self._init_sensor()
        v2x_sensors.append(self)
        logger.info(f"V2X Sensor {self.sensor_id} created")

    def _init_sensor(self):
        """Initialize the V2X sensor with GNSS and IMU sensors"""
        try:
            gnss_bp = self.world.get_blueprint_library().find('sensor.other.gnss')
            self.gnss_sensor = self.world.spawn_actor(
                gnss_bp, self.transform, attach_to=self.attach_to
            )

            imu_bp = self.world.get_blueprint_library().find('sensor.other.imu')
            self.imu_sensor = self.world.spawn_actor(
                imu_bp, self.transform, attach_to=self.attach_to
            )

            weak_self = weakref.ref(self)
            self.gnss_sensor.listen(lambda data: V2XSensor._on_gnss_data(weak_self, data))
            self.imu_sensor.listen(lambda data: V2XSensor._on_imu_data(weak_self, data))
            if self.config.enable_debug_visualization and self.attach_to is None:
                self.debug_sphere = self.world.debug.draw_point(
                    self.transform.location,
                    size=0.1,
                    color=carla.Color(0, 255, 0),
                    life_time=0
                )
        except Exception as e:
            logger.error(f"Failed to initialize sensor {self.sensor_id}: {e}")

    @staticmethod
    def _on_gnss_data(weak_self, gnss_data):
        """Callback for GNSS data"""
        self = weak_self()
        if not self or self._is_destroyed:
            return

        try:
            if self.gnss_sensor and self.gnss_sensor.is_alive:
                if self.attach_to and hasattr(self.attach_to, 'is_alive') and self.attach_to.is_alive:
                    self.location = self.attach_to.get_location()
                elif self.gnss_sensor and self.gnss_sensor.is_alive:
                    self.location = self.gnss_sensor.get_transform().location
                else:
                    return  # Skip if both sensors/actors are invalid

                self._check_cam_conditions()
        except (RuntimeError, AttributeError) as e:
            logger.debug(f"GNSS callback error for {self.sensor_id}: {e}")
            return

    @staticmethod
    def _on_imu_data(weak_self, imu_data):
        """Callback for IMU data"""
        self = weak_self()
        if not self or self._is_destroyed:
            return

        try:
            self.acceleration = imu_data.accelerometer
            self.gyroscope = imu_data.gyroscope

            if self.attach_to and hasattr(self.attach_to, 'is_alive') and self.attach_to.is_alive:
                if hasattr(self.attach_to, 'get_transform'):
                    self.heading = self.attach_to.get_transform().rotation.yaw

                if hasattr(self.attach_to, 'get_velocity'):
                    velocity = self.attach_to.get_velocity()
                    self.speed = math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)
                    self.yaw_rate = self.gyroscope.z
        except (RuntimeError, AttributeError) as e:
            logger.debug(f"IMU callback error for {self.sensor_id}: {e}")
            return

    def add_message_handler(self, handler: Callable[[CAMData], None]) -> None:
        """Add a message handler function that will be called for each received message"""
        self.message_handlers.append(handler)

    def add_message_filter(self, filter_func: Callable[[CAMData], bool]) -> None:
        """Add a message filter function. Only messages passing all filters will be processed"""
        self.message_filters.append(filter_func)

    def set_extensions_provider(self, provider: Callable[[], Dict]) -> None:
        """Set a function that provides custom extensions for outgoing CAM messages"""
        self.extensions_provider = provider

    def _check_cam_conditions(self):
        """Check ETSI CAM standard triggering conditions"""
        current_time = time.time()
        should_send = False
        
        # Always check maximum time condition
        if current_time - self.last_cam_time >= self.config.gen_cam_max:
            should_send = True
            logger.debug(f"CAM triggered for {self.sensor_id}: Maximum time elapsed")

        # Check minimum time condition
        elif current_time - self.last_cam_time < self.config.gen_cam_min:
            return

        # Check other triggering conditions
        elif self.previous_location and self.attach_to:
            # Check heading change
            if self.previous_heading is not None and hasattr(self, 'heading'):
                heading_diff = abs(self.heading - self.previous_heading)
                if heading_diff > self.config.heading_threshold or heading_diff > (360 - self.config.heading_threshold):
                    should_send = True
                    logger.debug(f"CAM triggered for {self.sensor_id}: Heading change {heading_diff:.2f}°")

            # Check position change
            if self.location:
                distance = math.sqrt(
                    (self.location.x - self.previous_location.x) ** 2 +
                    (self.location.y - self.previous_location.y) ** 2 +
                    (self.location.z - self.previous_location.z) ** 2
                )
                if distance > self.config.position_threshold:
                    should_send = True
                    logger.debug(f"CAM triggered for {self.sensor_id}: Position change {distance:.2f}m")

            # Check speed change
            if hasattr(self, 'speed') and hasattr(self, 'previous_speed'):
                speed_diff = abs(self.speed - self.previous_speed)
                if speed_diff > self.config.speed_threshold:
                    should_send = True
                    logger.debug(f"CAM triggered for {self.sensor_id}: Speed change {speed_diff:.2f}m/s")

        # Check low frequency container time
        low_freq_elapsed = current_time - self.last_low_freq_time
        include_low_freq = low_freq_elapsed >= self.config.low_freq_interval

        if should_send:
            self._send_cam(include_low_freq)
            self.last_cam_time = current_time

            # Store current values as previous
            if self.location:
                self.previous_location = carla.Location(
                    x=self.location.x, y=self.location.y, z=self.location.z
                )
            if hasattr(self, 'heading'):
                self.previous_heading = self.heading
            if hasattr(self, 'speed'):
                self.previous_speed = self.speed

            if include_low_freq:
                self.last_low_freq_time = current_time

    def _send_cam(self, include_low_freq=False):
        """Send Cooperative Awareness Message (CAM)"""
        if self.location is None:
            return

        # Prepare vehicle data
        vehicle_data = None
        if self.attach_to:
            velocity = self.attach_to.get_velocity()
            acceleration = getattr(self, 'acceleration', carla.Vector3D(0, 0, 0))
            
            vehicle_data = {
                "position": {
                    "x": self.location.x,
                    "y": self.location.y,
                    "z": self.location.z
                },
                "heading": getattr(self, 'heading', 0.0),
                "speed": getattr(self, 'speed', 0.0),
                "velocity": {
                    "x": velocity.x,
                    "y": velocity.y,
                    "z": velocity.z
                },
                "acceleration": {
                    "x": acceleration.x,
                    "y": acceleration.y,
                    "z": acceleration.z
                } if acceleration else None,
                "yaw_rate": getattr(self, 'yaw_rate', 0.0)
            }

        # Get custom extensions if provider is set
        extensions = {}
        if self.extensions_provider:
            try:
                extensions = self.extensions_provider()
            except Exception as e:
                logger.warning(f"Extensions provider failed for {self.sensor_id}: {e}")

        # Create CAM message
        station_type_override = None
        try:
            if self.attach_to and hasattr(self.attach_to, "type_id"):
                tid = self.attach_to.type_id
                if isinstance(tid, str):
                    if tid.startswith("vehicle."):
                        station_type_override = "passenger-car"
                    elif tid.startswith("traffic.traffic_light") or tid.startswith("traffic."):
                        station_type_override = "road-side-unit"
        except Exception:
            station_type_override = station_type_override  # no-op

        cam_data = CAMData(
            sender_id=self.sensor_id,
            timestamp=datetime.now(),
            vehicle_data=vehicle_data,
            extensions=extensions,
            station_type_override=station_type_override,
            include_vehicle_data_container=getattr(self.config, "include_vehicle_data_container", False)
        )
        
        logger.debug(f"Sensor {self.sensor_id} sending CAM: {cam_data}")
        # Calculate transmission range
        max_path_loss = abs(self.config.transmit_power - self.config.receiver_sensitivity)
        frequency_loss = 20 * math.log10(self.config.frequency * 1000) + 32.44
        max_distance = min(
            10 ** ((max_path_loss - frequency_loss) / 20) * 1000,
            self.config.filter_distance
        )
        max_distance = 50

        # Broadcast to all other V2X sensors within range
        for sensor in v2x_sensors:
            if sensor.sensor_id != self.sensor_id and sensor.location is not None:
                distance = math.sqrt(
                    (self.location.x - sensor.location.x) ** 2 +
                    (self.location.y - sensor.location.y) ** 2 +
                    (self.location.z - sensor.location.z) ** 2
                )

                if distance <= max_distance:
                    sensor.receive_cam(cam_data)

                    # Debug visualization
                    if self.config.enable_debug_visualization:
                        self.world.debug.draw_line(
                            self.location,
                            sensor.location,
                            color=carla.Color(0, 0, 255),
                            life_time=self.config.gen_cam_min
                        )

    def receive_cam(self, cam_data: CAMData):
        """Receive and process a CAM message"""
        for filter_func in self.message_filters:
            if not filter_func(cam_data):
                logger.debug(f"Sensor {self.sensor_id} - CAM from {cam_data.sender_id} filtered out")
                return

        self.received_messages.append(cam_data)
        if len(self.received_messages) > self.config.max_message_history:
            self.received_messages = self.received_messages[-self.config.max_message_history:]
        
        for handler in self.message_handlers:
            try:
                handler(cam_data)
            except Exception as e:
                logger.error(f"Message handler failed for {self.sensor_id}: {e}")

        logger.debug(f"Sensor {self.sensor_id} received CAM from {cam_data.sender_id}")

    def get_recent_messages(self, max_age: Optional[float] = None) -> List[CAMData]:
        """Get recent messages, optionally filtered by age"""
        if max_age is None:
            return self.received_messages.copy()
        
        cutoff_time = datetime.now().timestamp() - max_age
        return [msg for msg in self.received_messages 
                if msg.timestamp.timestamp() > cutoff_time]

    def get_messages_from_sender(self, sender_id: str, max_age: Optional[float] = None) -> List[CAMData]:
        """Get messages from a specific sender"""
        messages = self.get_recent_messages(max_age)
        return [msg for msg in messages if msg.sender_id == sender_id]

    def get_latest_message_from_sender(self, sender_id: str, max_age: Optional[float] = None) -> Optional[CAMData]:
        """Get the latest message from a specific sender"""
        messages = self.get_messages_from_sender(sender_id, max_age)
        return messages[-1] if messages else None

    def is_communication_active(self, sender_id: str, timeout: Optional[float] = None) -> bool:
        """Check if communication with a specific sender is active"""
        if timeout is None:
            timeout = self.config.communication_timeout
            
        latest = self.get_latest_message_from_sender(sender_id, timeout)
        return latest is not None

    def get_communication_status(self, sender_ids: Optional[List[str]] = None) -> Dict[str, bool]:
        """Get communication status with specified senders or all known senders"""
        if sender_ids is None:
            # Get all unique sender IDs from recent messages
            sender_ids = list(set(msg.sender_id for msg in self.get_recent_messages(10.0)))
        
        return {sender_id: self.is_communication_active(sender_id) 
                for sender_id in sender_ids}

    def destroy(self):
        """Clean up the sensor"""
        try:
            self._is_destroyed = True
            
            if self.gnss_sensor is not None and self.gnss_sensor.is_alive:
                self.gnss_sensor.stop()
                self.gnss_sensor.destroy()
                self.gnss_sensor = None

            if self.imu_sensor is not None and self.imu_sensor.is_alive:
                self.imu_sensor.stop()
                self.imu_sensor.destroy()
                self.imu_sensor = None

            self.received_messages.clear()
            self.message_handlers.clear()
            self.message_filters.clear()

            if self in v2x_sensors:
                v2x_sensors.remove(self)

            logger.info(f"V2X Sensor {self.sensor_id} destroyed")
        except Exception as e:
            logger.error(f"Error destroying V2X sensor {self.sensor_id}: {e}")
            self._is_destroyed = True


# Utility functions for common message filtering and handling
class V2XUtils:
    """Utility functions for common V2X operations"""
    
    @staticmethod
    def create_extension_filter(extension_key: str, extension_value: Any = None) -> Callable[[CAMData], bool]:
        """Create a filter that only passes messages with a specific extension"""
        def filter_func(cam_data: CAMData) -> bool:
            if extension_value is None:
                return extension_key in cam_data.extensions
            return cam_data.extensions.get(extension_key) == extension_value
        return filter_func

    @staticmethod
    def create_sender_filter(sender_ids: List[str]) -> Callable[[CAMData], bool]:
        """Create a filter that only passes messages from specific senders"""
        def filter_func(cam_data: CAMData) -> bool:
            return cam_data.sender_id in sender_ids
        return filter_func

    @staticmethod
    def create_distance_filter(sensor: V2XSensor, max_distance: float) -> Callable[[CAMData], bool]:
        """Create a filter that only passes messages from senders within a certain distance"""
        def filter_func(cam_data: CAMData) -> bool:
            if not cam_data.position or not sensor.location:
                return True  # Can't filter without position data
            
            distance = math.sqrt(
                (sensor.location.x - cam_data.position['x']) ** 2 +
                (sensor.location.y - cam_data.position['y']) ** 2 +
                (sensor.location.z - cam_data.position['z']) ** 2
            )
            return distance <= max_distance
        return filter_func

    @staticmethod
    def create_logging_handler(log_level: int = logging.INFO) -> Callable[[CAMData], None]:
        """Create a message handler that logs received messages"""
        def handler(cam_data: CAMData) -> None:
            logger.log(log_level, f"Received CAM: {cam_data}")
        return handler

    @staticmethod
    def create_config_for_use_case(use_case: str) -> V2XSensorConfig:
        """Create optimized configuration for specific use cases"""
        config = V2XSensorConfig()
        
        if use_case == "platoon":
            # Tighter coordination for platooning
            config.gen_cam_min = 0.05
            config.gen_cam_max = 0.5
            config.position_threshold = 2.0
            config.heading_threshold = 2.0
            config.speed_threshold = 2.0
            config.low_freq_interval = 0.2
            
        elif use_case == "intersection":
            # Faster updates for intersection management
            config.gen_cam_min = 0.1
            config.gen_cam_max = 0.3
            config.position_threshold = 1.0
            config.heading_threshold = 2.0
            config.speed_threshold = 1.0
            
        elif use_case == "highway":
            # Standard parameters for highway driving
            config.gen_cam_min = 0.1
            config.gen_cam_max = 1.0
            config.position_threshold = 4.0
            config.heading_threshold = 4.0
            config.speed_threshold = 5.0
            
        elif use_case == "low_bandwidth":
            # Conservative parameters for limited bandwidth
            config.gen_cam_min = 0.5
            config.gen_cam_max = 2.0
            config.position_threshold = 10.0
            config.heading_threshold = 10.0
            config.speed_threshold = 10.0
            
        return config
