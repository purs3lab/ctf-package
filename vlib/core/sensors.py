import carla
import argparse
import random
import time
import math
import logging
import weakref
from queue import Queue
from queue import Empty
from datetime import datetime

logger = logging.getLogger(__name__)

v2x_sensors = []

class CAMData:
    """Class representing Cooperative Awareness Message (CAM) data"""
    def __init__(self, sender_id, timestamp, vehicle_data):
        self.sender_id = sender_id
        self.timestamp = timestamp
        self.vehicle_data = vehicle_data

        # ETSI CAM standard fields
        self.station_id = sender_id
        self.generation_delta_time = 0  # Time since last CAM in milliseconds

        # Basic container
        # TODO(sfx): Reorganize this. Currently this looks janky.
        self.station_type = "passenger-car" if vehicle_data else "road-side-unit"
        self.position = vehicle_data.get("position") if vehicle_data else None
        self.confidence = 0.95  # Position confidence

        # High frequency container
        self.heading = vehicle_data.get("heading") if vehicle_data else None
        self.speed = vehicle_data.get("speed") if vehicle_data else 0.0
        self.acceleration = vehicle_data.get("acceleration") if vehicle_data else 0.0
        self.yaw_rate = vehicle_data.get("yaw_rate") if vehicle_data else 0.0

        # Low frequency container
        self.vehicle_role = "default"
        self.path_history = []

    def __str__(self):
        if self.station_type == "passenger-car":
            return (f"CAM from {self.sender_id} - Position: {self.position}, "
                   f"Speed: {self.speed:.2f} m/s, Heading: {self.heading:.2f}°, "
                   f"Time: {self.timestamp}")
        else:
            return f"CAM from {self.sender_id} (RSU) - Position: {self.position}, Time: {self.timestamp}"

class V2XSensor:
    """Custom V2X sensor implementation based on ETSI CAM standard"""
    def __init__(self, world, attach_to=None, sensor_id=None, transform=None,
                 transmit_power=21.5, receiver_sensitivity=-99, frequency=5.9,
                 gen_cam_min=0.1, gen_cam_max=1.0, filter_distance=500):
        self.world = world
        self.sensor_id = sensor_id or f"v2x_{random.randint(1000, 9999)}"
        self.attach_to = attach_to
        self.transform = transform or carla.Transform()

        # V2X sensor parameters (based on CARLA V2X sensor blueprint attributes)
        self.transmit_power = transmit_power  # dBm
        self.receiver_sensitivity = receiver_sensitivity  # dBm
        self.frequency = frequency  # GHz
        self.filter_distance = filter_distance  # meters
        self.gen_cam_min = gen_cam_min  # seconds
        self.gen_cam_max = gen_cam_max  # seconds

        # Sensor state
        self.location = None
        self.previous_location = None
        self.previous_heading = None
        self.previous_speed = 0.0
        self.received_messages = []
        self.last_cam_time = time.time()
        self.last_low_freq_time = time.time()

        # Sensors for data collection
        self.gnss_sensor = None
        self.imu_sensor = None

        # Create a debug sphere to visualize the sensor
        self.debug_sphere = None

        # Initialize the sensor
        self._init_sensor()

        # Add to global list for communication
        v2x_sensors.append(self)
        logger.info(f"V2X Sensor {self.sensor_id} created")

    def _init_sensor(self):
        """Initialize the V2X sensor with GNSS and IMU sensors for data collection"""
        # Create a GNSS sensor for location data
        gnss_bp = self.world.get_blueprint_library().find('sensor.other.gnss')
        self.gnss_sensor = self.world.spawn_actor(
            gnss_bp,
            self.transform,
            attach_to=self.attach_to
        )

        # Create an IMU sensor for heading, acceleration, etc.
        imu_bp = self.world.get_blueprint_library().find('sensor.other.imu')
        self.imu_sensor = self.world.spawn_actor(
            imu_bp,
            self.transform,
            attach_to=self.attach_to
        )

        # Set up callbacks
        weak_self = weakref.ref(self)
        self.gnss_sensor.listen(lambda gnss_data: V2XSensor._on_gnss_data(weak_self, gnss_data))
        self.imu_sensor.listen(lambda imu_data: V2XSensor._on_imu_data(weak_self, imu_data))

        # Create a debug visualization to mark the sensor
        if self.attach_to is None:  # Only for stationary sensors
            self.debug_sphere = self.world.debug.draw_point(
                self.transform.location,
                size=0.1,
                color=carla.Color(0, 255, 0),  # green
                life_time=0  # persistent
            )

    @staticmethod
    def _on_gnss_data(weak_self, gnss_data):
        """Callback for GNSS data"""
        self = weak_self()
        if not self:
            return

        # Update sensor location
        if self.gnss_sensor and self.gnss_sensor.is_alive:
            if self.attach_to:
                # For vehicle-attached sensors, use the vehicle's location
                self.location = self.attach_to.get_location()
            else:
                # For stationary sensors, use the sensor's transform location
                self.location = self.gnss_sensor.get_transform().location

            # Check CAM triggering conditions
            self._check_cam_conditions()

    @staticmethod
    def _on_imu_data(weak_self, imu_data):
        """Callback for IMU data"""
        self = weak_self()
        if not self:
            return

        # Store IMU data for CAM generation
        self.acceleration = imu_data.accelerometer
        self.gyroscope = imu_data.gyroscope

        # For vehicles, get heading from the vehicle
        if self.attach_to and hasattr(self.attach_to, 'get_transform'):
            self.heading = self.attach_to.get_transform().rotation.yaw

            # Calculate speed if we have a vehicle
            if hasattr(self.attach_to, 'get_velocity'):
                velocity = self.attach_to.get_velocity()
                self.speed = math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)

                # Calculate yaw rate from gyroscope
                self.yaw_rate = self.gyroscope.z

    def _check_cam_conditions(self):
        """Check ETSI CAM standard triggering conditions"""
        current_time = time.time()
        should_send = False
        
        # Get role name upfront
        role_name = self.attach_to.attributes.get('role_name', 'unknown') if self.attach_to else 'stationary'

        # Always check maximum time condition
        if current_time - self.last_cam_time >= self.gen_cam_max:
            should_send = True
            logger.debug(f"CAM triggered for {role_name}: Maximum time elapsed ({self.gen_cam_max}s)")

        # Check minimum time condition
        elif current_time - self.last_cam_time < self.gen_cam_min:
            return  # Don't send if minimum time hasn't elapsed

        # Check other conditions only if we have previous data and are attached to a vehicle
        elif self.previous_location and self.attach_to:
            # Check heading change > 4 degrees
            if self.previous_heading is not None and hasattr(self, 'heading'):
                heading_diff = abs(self.heading - self.previous_heading)
                if heading_diff > 4.0 or heading_diff > 356.0:  # Handle wrap-around
                    should_send = True
                    logger.debug(f"CAM triggered for {role_name}: Heading change {heading_diff:.2f}°")

            # Check position change > 4 meters
            if self.location:
                distance = math.sqrt(
                    (self.location.x - self.previous_location.x) ** 2 +
                    (self.location.y - self.previous_location.y) ** 2 +
                    (self.location.z - self.previous_location.z) ** 2
                )
                if distance > 4.0:
                    should_send = True
                    logger.debug(f"CAM triggered for {role_name}: Position change {distance:.2f}m")

            # Check speed change > 5 m/s
            if hasattr(self, 'speed') and hasattr(self, 'previous_speed'):
                speed_diff = abs(self.speed - self.previous_speed)
                if speed_diff > 5.0:
                    should_send = True
                    logger.debug(f"CAM triggered for {role_name}: Speed change {speed_diff:.2f}m/s")

        # Check low frequency container time (500ms)
        low_freq_elapsed = current_time - self.last_low_freq_time
        include_low_freq = low_freq_elapsed >= 0.5  # 500ms

        if should_send:
            self._send_cam(include_low_freq)
            self.last_cam_time = current_time

            # Store current values as previous
            if self.location:
                self.previous_location = carla.Location(
                    x=self.location.x,
                    y=self.location.y,
                    z=self.location.z
                )
            if hasattr(self, 'heading'):
                self.previous_heading = self.heading
            if hasattr(self, 'speed'):
                self.previous_speed = self.speed

            # Update low frequency time if included
            if include_low_freq:
                self.last_low_freq_time = current_time

    def _send_cam(self, include_low_freq=False):
        """Send Cooperative Awareness Message (CAM)"""
        if self.location is None:
            return

        # Prepare vehicle data
        vehicle_data = None
        if self.attach_to:
            vehicle_data = {
                "position": {
                    "x": self.location.x,
                    "y": self.location.y,
                    "z": self.location.z
                },
                "heading": getattr(self, 'heading', 0.0),
                "speed": getattr(self, 'speed', 0.0),
                "acceleration": getattr(self, 'acceleration', None),
                "yaw_rate": getattr(self, 'yaw_rate', 0.0)
            }

        # Create CAM message
        cam_data = CAMData(
            sender_id=self.sensor_id,
            timestamp=datetime.now(),
            vehicle_data=vehicle_data
        )

        # Calculate transmission range based on transmit power and receiver sensitivity
        # This is a simplified model; real V2X would use path loss models
        max_path_loss = abs(self.transmit_power - self.receiver_sensitivity)  # dB

        # Simple free space path loss model: FSPL = 20*log10(d) + 20*log10(f) + 32.44
        # Solving for d: d = 10^((FSPL - 20*log10(f) - 32.44) / 20)
        frequency_loss = 20 * math.log10(self.frequency * 1000) + 32.44  # Convert GHz to MHz
        max_distance = min(
            10 ** ((max_path_loss - frequency_loss) / 20) * 1000,  # Convert to meters
            self.filter_distance  # Cap at filter distance
        )

        # Broadcast to all other V2X sensors within range
        for sensor in v2x_sensors:
            if sensor.sensor_id != self.sensor_id:  # Don't send to self
                # Check if within range
                if sensor.location is not None:
                    distance = math.sqrt(
                        (self.location.x - sensor.location.x) ** 2 +
                        (self.location.y - sensor.location.y) ** 2 +
                        (self.location.z - sensor.location.z) ** 2
                    )

                    if distance <= max_distance:
                        sensor.receive_cam(cam_data)

                        # Draw debug line to visualize communication
                        self.world.debug.draw_line(
                            self.location,
                            sensor.location,
                            color=carla.Color(0, 0, 255),  # blue
                            life_time=0.3
                        )
                    else:
                        logger.debug(f"Sensor {sensor.sensor_id} is out of range from {self.sensor_id} (distance: {distance:.2f}m) max_distance: {max_distance:.2f}m")

    def receive_cam(self, cam_data):
        """Receive and process a CAM message"""
        self.received_messages.append(cam_data)
        
        # Check if this is the player sensor receiving from target sensor
        if self.sensor_id == "player_sensor" and cam_data.sender_id == "target_sensor":
            logger.info("FLAG{yooo!}")
        logger.info(f"Sensor {self.sensor_id} received CAM from {cam_data.sender_id}: {cam_data}")

    def destroy(self):
        """Clean up the sensor"""
        try:
            if self.gnss_sensor is not None and self.gnss_sensor.is_alive:
                self.gnss_sensor.stop()
                self.gnss_sensor.destroy()
                self.gnss_sensor = None

            if self.imu_sensor is not None and self.imu_sensor.is_alive:
                self.imu_sensor.stop()
                self.imu_sensor.destroy()
                self.imu_sensor = None

            # Remove from global list
            if self in v2x_sensors:
                v2x_sensors.remove(self)

            logger.info(f"V2X Sensor {self.sensor_id} destroyed")
        except Exception as e:
            logger.error(f"Error destroying V2X Sensor {self.sensor_id}: {e}")
