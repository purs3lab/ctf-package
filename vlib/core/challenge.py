import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Dict, Any

import carla

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

    def __init__(self, challenge_id: str, name: str, description: str):
        """
        Initialize a challenge.

        Args:
            challenge_id: Unique identifier for the challenge
            name: Human-readable name of the challenge
            description: Description of what the challenge involves
        """
        self.challenge_id = challenge_id
        self.name = name
        self.description = description

        # Runtime state
        self.status = ChallengeStatus.NOT_STARTED
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.score: int = 0
        self.max_score: int = 100

        # CARLA objects - will be set during setup
        self.world: Optional[carla.World] = None
        self.spawned_actors: list = []
        self.sensors: list = []

        logger.info(f"Challenge '{self.name}' ({self.challenge_id}) initialized")

    @abstractmethod
    def setup(self, world: carla.World, client: carla.Client = None) -> bool:
        """
        Set up the challenge in the CARLA world.

        This method should spawn any necessary actors, set up sensors,
        configure the world state, etc.

        Args:
            world: The CARLA world instance

        Returns:
            bool: True if setup was successful, False otherwise
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

        # Clean up spawned actors
        try:
            for actor in self.spawned_actors:
                if actor is not None and actor.is_alive:
                    actor.destroy()
            self.spawned_actors.clear()

            # Clean up sensors
            for sensor in self.sensors:
                if sensor is not None and sensor.is_alive:
                    sensor.destroy()
            self.sensors.clear()

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