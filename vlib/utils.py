#!/usr/bin/env python

import logging
import random
from typing import Optional

import carla

logger = logging.getLogger(__name__)


def get_player_vehicle(world: carla.World) -> Optional[carla.Vehicle]:
    """Get the player vehicle from the world"""
    if not world:
        logger.warning("World not set, cannot find player vehicle")
        return None

    actors = world.get_actors()
    for actor in actors:
        if actor.type_id.startswith('vehicle.') and hasattr(actor, 'attributes'):
            role_name = actor.attributes.get('role_name', '')
            if role_name == 'hero':
                return actor
    return None


def get_vehicle(world: carla.World, role_name: str) -> Optional[carla.Vehicle]:
    """Get a vehicle by its role name from the world"""
    if not world:
        logger.warning("World not set, cannot find vehicle")
        return None

    actors = world.get_actors()
    for actor in actors:
        if actor.type_id.startswith('vehicle.') and hasattr(actor, 'attributes'):
            if actor.attributes.get('role_name', '') == role_name:
                return actor
    return None


def deploy_vehicle(world: carla.World, role_name: str, autopilot: bool, 
                  spawned_actors: list) -> Optional[carla.Vehicle]:
    """Deploy a vehicle with the specified role name in the world"""
    
    if not world:
        logger.warning("World not set, cannot deploy vehicle")
        return None

    # Check if the vehicle already exists
    existing_vehicle = get_vehicle(world, role_name)
    if existing_vehicle:
        logger.info(f"Vehicle with role name '{role_name}' already exists: {existing_vehicle.type_id}")
        return existing_vehicle
            
    logger.info(f"Deploying vehicle with role name: {role_name}")
    
    # Get available vehicle blueprints
    vehicles = world.get_blueprint_library().filter('vehicle.*')
    if not vehicles:
        logger.warning("No vehicle blueprints found")
        return None
    
    # Choose a suitable vehicle blueprint (prefer cars for platoon)
    blueprint = None
    for bp in vehicles:
        if 'sedan' in bp.id.lower() or 'coupe' in bp.id.lower():
            blueprint = bp
            break
    
    if not blueprint:
        blueprint = vehicles[0]  # Fallback to first available
    
    logger.debug(f"Using vehicle blueprint: {blueprint.id}")
    blueprint.set_attribute('role_name', role_name)
    
    spawn_points = world.get_map().get_spawn_points()
    
    # Try multiple spawn points to avoid collisions
    vehicle = None
    max_attempts = 15
    
    for attempt in range(max_attempts):
        try:
            spawn_point = random.choice(spawn_points)
            vehicle = world.spawn_actor(blueprint, spawn_point)
            break
        except RuntimeError as e:
            if attempt == max_attempts - 1:
                logger.error(f"Failed to spawn vehicle {role_name} after {max_attempts} attempts")
                raise e
            continue

    # Set autopilot if required
    if autopilot:
        vehicle.set_autopilot(True)
        logger.info(f"Vehicle {role_name} set to autopilot")

    logger.info(f"Deployed {role_name} vehicle: {vehicle.type_id} at {vehicle.get_transform().location}")
    spawned_actors.append(vehicle)
    return vehicle