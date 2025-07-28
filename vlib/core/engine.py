#!/usr/bin/env python

"""
vlib - Challenge Engine

Engine for managing and orchestrating multiple CTF challenges.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Callable

import carla
from vlib.core.challenge import Challenge, ChallengeStatus

logger = logging.getLogger(__name__)


class ChallengeEngine:
    """
    Engine for managing multiple CTF challenges.

    Handles challenge registration, lifecycle management, and status polling.
    """

    def __init__(self, world: carla.World, poll_interval: float = 1.0):
        """
        Initialize the Challenge Engine.

        Args:
            world: CARLA world instance
            poll_interval: How often to check challenge status (seconds)
        """
        self.world = world
        self.poll_interval = poll_interval

        # Challenge management
        self.challenges: Dict[str, Challenge] = {}
        self.active_challenges: Dict[str, Challenge] = {}

        # Polling control
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_challenge_completed: Optional[Callable[[Challenge], None]] = None
        self.on_challenge_failed: Optional[Callable[[Challenge], None]] = None

        logger.info("Challenge Engine initialized")

    def register_challenge(self, challenge: Challenge) -> bool:
        """
        Register a new challenge with the engine.

        Args:
            challenge: Challenge instance to register

        Returns:
            bool: True if registration successful, False otherwise
        """
        if challenge.challenge_id in self.challenges:
            logger.warning(f"Challenge {challenge.challenge_id} already registered")
            return False

        self.challenges[challenge.challenge_id] = challenge
        logger.info(f"Registered challenge: {challenge.name} ({challenge.challenge_id})")
        return True

    def unregister_challenge(self, challenge_id: str) -> bool:
        """
        Unregister a challenge from the engine.

        Args:
            challenge_id: ID of challenge to unregister

        Returns:
            bool: True if unregistration successful, False otherwise
        """
        if challenge_id not in self.challenges:
            logger.warning(f"Challenge {challenge_id} not found")
            return False

        # Stop the challenge if it's running
        if challenge_id in self.active_challenges:
            self.stop_challenge(challenge_id)

        challenge = self.challenges.pop(challenge_id)
        logger.info(f"Unregistered challenge: {challenge.name} ({challenge_id})")
        return True

    def start_challenge(self, challenge_id: str) -> bool:
        """
        Start a specific challenge.

        Args:
            challenge_id: ID of challenge to start

        Returns:
            bool: True if started successfully, False otherwise
        """
        if challenge_id not in self.challenges:
            logger.error(f"Challenge {challenge_id} not found")
            return False

        challenge = self.challenges[challenge_id]

        # Setup the challenge first
        if not challenge.setup(self.world):
            logger.error(f"Failed to setup challenge {challenge_id}")
            return False

        # Start the challenge
        if challenge.start():
            self.active_challenges[challenge_id] = challenge
            logger.info(f"Started challenge: {challenge.name}")
            return True
        else:
            logger.error(f"Failed to start challenge {challenge_id}")
            return False

    def stop_challenge(self, challenge_id: str) -> bool:
        """
        Stop a specific challenge.

        Args:
            challenge_id: ID of challenge to stop

        Returns:
            bool: True if stopped successfully, False otherwise
        """
        if challenge_id not in self.active_challenges:
            logger.warning(f"Challenge {challenge_id} is not active")
            return False

        challenge = self.active_challenges.pop(challenge_id)
        result = challenge.stop()

        if result:
            logger.info(f"Stopped challenge: {challenge.name}")
        else:
            logger.error(f"Error stopping challenge: {challenge.name}")

        return result

    def get_challenge(self, challenge_id: str) -> Optional[Challenge]:
        """
        Get a challenge by ID.

        Args:
            challenge_id: ID of challenge to retrieve

        Returns:
            Challenge instance or None if not found
        """
        return self.challenges.get(challenge_id)

    def get_active_challenges(self) -> List[Challenge]:
        """
        Get list of currently active challenges.

        Returns:
            List of active Challenge instances
        """
        return list(self.active_challenges.values())

    def get_all_challenges(self) -> List[Challenge]:
        """
        Get list of all registered challenges.

        Returns:
            List of all Challenge instances
        """
        return list(self.challenges.values())

    def start_polling(self):
        """Start the challenge status polling thread."""
        if self._polling:
            logger.warning("Polling already started")
            return

        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_challenges, daemon=True)
        self._poll_thread.start()
        logger.info("Started challenge status polling")

    def stop_polling(self):
        """Stop the challenge status polling thread."""
        if not self._polling:
            return

        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        logger.info("Stopped challenge status polling")

    def _poll_challenges(self):
        """
        Main polling loop for checking challenge status.
        Runs in a separate thread.
        """
        while self._polling:
            try:
                # Make a copy to avoid modification during iteration
                active_challenges = list(self.active_challenges.items())

                for challenge_id, challenge in active_challenges:
                    self._check_challenge_status(challenge_id, challenge)

                time.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Error in challenge polling: {e}")
                time.sleep(self.poll_interval)

    def _check_challenge_status(self, challenge_id: str, challenge: Challenge):
        """
        Check the status of a single challenge and handle state changes.

        Args:
            challenge_id: ID of the challenge
            challenge: Challenge instance
        """
        try:
            # Check for completion, but don't stop the challenge
            if challenge.status != ChallengeStatus.COMPLETED and challenge.check_completion():
                challenge.status = ChallengeStatus.COMPLETED
                # Keep challenge in active_challenges - don't remove it
                # Don't call challenge.stop() - keep it running
                logger.info(f"Challenge {challenge.name} completed with score: {challenge.score} - keeping challenge running")

                if self.on_challenge_completed:
                    self.on_challenge_completed(challenge)
                return

            # Challenge is still running (or completed but continuing)
            status_text = "completed but continuing" if challenge.status == ChallengeStatus.COMPLETED else "running"
            logger.debug(f"Challenge {challenge.name} {status_text} - "f"Elapsed time: {challenge.get_elapsed_time():.1f}s")

        except Exception as e:
            logger.error(f"Error checking challenge {challenge_id}: {e}")
            challenge.status = ChallengeStatus.FAILED
            self.active_challenges.pop(challenge_id, None)
            challenge.stop()

            if self.on_challenge_failed:
                self.on_challenge_failed(challenge)

    def stop_all_challenges(self):
        """Stop all active challenges and clean up."""
        logger.info("Stopping all active challenges")

        # Stop polling first
        self.stop_polling()

        # Stop all active challenges
        challenge_ids = list(self.active_challenges.keys())
        for challenge_id in challenge_ids:
            self.stop_challenge(challenge_id)

        logger.info("All challenges stopped")

    def get_status_summary(self) -> dict:
        """
        Get a summary of all challenges and their statuses.

        Returns:
            dict: Summary of challenge statuses
        """
        return {
            'total_challenges': len(self.challenges),
            'active_challenges': len(self.active_challenges),
            'challenges': [challenge.to_dict() for challenge in self.challenges.values()]
        }