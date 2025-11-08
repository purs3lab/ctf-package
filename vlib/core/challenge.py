import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, override

import carla

from vlib.core.sensors import V2XSensor
from vlib.core.websocket_bridge import V2XWebSocketBridge

logger = logging.getLogger(__name__)


class ChallengeStatus(Enum):
    """Status of a challenge"""
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Challenge(ABC):
    """
    Base class for all CTF challenges.

    This class provides the interface that all challenges must implement.
    """

    def __init__(self, challenge_id: str, name: str, description: str, enable_websocket: bool = True):
        """
        Initialize a challenge.

        Args:
            challenge_id: Unique identifier for the challenge
            name: Human-readable name of the challenge
            description: Description of what the challenge involves
            enable_websocket: Whether to enable WebSocket V2X bridge for this challenge
        """
        self.challenge_id = challenge_id
        self.name = name
        self.description = description
        self.enable_websocket = enable_websocket

        self.status = ChallengeStatus.NOT_STARTED
        self.start_time: float = 0
        self.end_time: float = 0
        self.score: int = 0
        self.max_score: int = 100

        self.world: carla.World | None = None
        self.spawned_actors: list[carla.Vehicle] = []
        self.sensors: list[V2XSensor] = []
        
        self.websocket_bridge: V2XWebSocketBridge | None = None
        self.player_vehicle: carla.Vehicle | None = None

        logger.info(f"Challenge '{self.name}' ({self.challenge_id}) initialized (WebSocket: {enable_websocket})")

    @abstractmethod
    def setup(self, world: carla.World, client: carla.Client) -> bool:
        """
        Set up the challenge in the CARLA world.

        This method should spawn any necessary actors, set up sensors,
        configure the world state, etc.

        Args:
            world: The CARLA world instance
            client: The client object.

        Returns:
            bool: True if setup was successful, False otherwise. 
            Make sure this is checked when passing the challenge to the 
            Challenge Engine
        """
        pass

    @abstractmethod
    def check_completion(self) -> bool:
        """
        Check if the challenge has been completed.

        This method is called periodically to check if the challenge
        objectives have been met.

        Returns:
            bool: True if the challenge is complete, False otherwise
        """
        pass

    def start(self) -> bool:
        """
        Start the challenge.

        Returns:
            bool: True if started successfully, False otherwise
        """
        if self.status != ChallengeStatus.NOT_STARTED:
            logger.warning(f"Challenge {self.challenge_id} is already started or completed")
            return False

        self.status = ChallengeStatus.RUNNING
        self.start_time = time.time()
        
        if self.enable_websocket:
            self._start_websocket_bridge()
        
        logger.info(f"Challenge '{self.name}' started")
        return True

    def stop(self) -> bool:
        """
        Stop the challenge and clean up resources.

        Returns:
            bool: True if stopped successfully, False otherwise
        """
        if self.status == ChallengeStatus.RUNNING:
            self.end_time = time.time()
            self.status = ChallengeStatus.COMPLETED

        if self.websocket_bridge:
            self._stop_websocket_bridge()

        try:
            for sensor in self.sensors:
                if sensor is not None and hasattr(sensor, 'destroy'):
                    try:
                        sensor.destroy()
                    except Exception as e:
                        logger.error(f"Error destroying sensor: {e}")
            self.sensors.clear()
            
            from vlib.core.sensors import v2x_sensors
            sensors_to_remove = []
            for sensor in v2x_sensors:
                if (hasattr(sensor, 'attach_to') and sensor.attach_to and 
                    sensor.attach_to in self.spawned_actors):
                    sensors_to_remove.append(sensor)
            
            for sensor in sensors_to_remove:
                try:
                    sensor.destroy()
                except Exception as e:
                    logger.error(f"Error destroying V2X sensor {sensor.sensor_id}: {e}")

            for actor in self.spawned_actors:
                if actor is not None and actor.is_alive:
                    try:
                        actor.destroy()
                    except Exception as e:
                        logger.error(f"Error destroying actor {actor.id}: {e}")
            self.spawned_actors.clear()

            logger.info(f"Challenge '{self.name}' stopped and cleaned up")
            return True

        except Exception as e:
            logger.error(f"Error during challenge cleanup: {e}")
            return False

    def get_elapsed_time(self) -> float:
        """
        Get the elapsed time since the challenge started.

        Returns:
            float: Elapsed time in seconds, or 0 if not started
        """
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert challenge information to dictionary format.

        Returns:
            dict: Challenge information as dictionary
        """
        return {
            'challenge_id': self.challenge_id,
            'name': self.name,
            'description': self.description,
            'status': self.status.value,
            'score': self.score,
            'max_score': self.max_score,
            'elapsed_time': self.get_elapsed_time()
        }
    
    def _find_player_vehicle(self) -> carla.Vehicle | None:
        """
        Find the player vehicle (hero vehicle) in the world.
        
        Returns:
            carla.Vehicle: The player vehicle if found, None otherwise
        """
        if not self.world:
            logger.debug("World not available, cannot find player vehicle")
            return None
            
        try:
            # Look for vehicle with role_name 'hero'
            for actor in self.world.get_actors().filter('vehicle.*'):
                if actor.attributes.get('role_name') == 'hero':
                    logger.debug(f"Found player vehicle: {actor.type_id} (ID: {actor.id})")
                    return actor
                    
            logger.debug("Player vehicle (role_name='hero') not found in world")
            return None
            
        except Exception as e:
            logger.error(f"Error finding player vehicle: {e}")
            return None
    
    def _start_websocket_bridge(self):
        """Start the WebSocket bridge for V2X communication"""
        if not self.world:
            logger.warning("World not available, cannot start WebSocket bridge")
            return
            
        # Find player vehicle
        self.player_vehicle = self._find_player_vehicle()
        if not self.player_vehicle:
            logger.warning("Cannot start WebSocket bridge: player vehicle not found")
            return
            
        try:
            # Create and start WebSocket bridge
            self.websocket_bridge = V2XWebSocketBridge(
                player_vehicle=self.player_vehicle,
                world=self.world,
                port=4000
            )
            
            # Start server in background thread
            self.websocket_bridge.start_server_thread()
            logger.info(f"WebSocket bridge started for challenge '{self.name}' on port 4000")
            
        except Exception as e:
            logger.error(f"Failed to start WebSocket bridge: {e}")
            self.websocket_bridge = None
    
    def _stop_websocket_bridge(self):
        """Stop the WebSocket bridge"""
        if self.websocket_bridge:
            try:
                self.websocket_bridge.stop()
                self.websocket_bridge = None
                logger.info("WebSocket bridge stopped")
            except Exception as e:
                logger.error(f"Error stopping WebSocket bridge: {e}")
