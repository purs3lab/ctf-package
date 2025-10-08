#!/usr/bin/env python

import logging
import random

import carla

logger = logging.getLogger(__name__)


def get_player_vehicle(world: carla.World) -> carla.Vehicle | None:
    """Get the player vehicle from the world"""
    if not world:
        logger.warning("World not set, cannot find player vehicle")
        return None

    actors = world.get_actors()
    for actor in actors:
        if actor.type_id.startswith("vehicle.") and hasattr(actor, "attributes"):
            role_name = actor.attributes.get("role_name", "")
            if role_name == "hero":
                return actor
    return None


def get_vehicle(world: carla.World, role_name: str) -> carla.Vehicle | None:
    """Get a vehicle by its role name from the world"""
    if not world:
        logger.warning("World not set, cannot find vehicle")
        return None

    actors = world.get_actors()
    for actor in actors:
        if actor.type_id.startswith("vehicle.") and hasattr(actor, "attributes"):
            if actor.attributes.get("role_name", "") == role_name:
                return actor
    return None


def deploy_vehicle_at_location(world: carla.World, role_name: str, transform: carla.Transform, spawned_actors: list[carla.Vehicle]) -> carla.Vehicle:
    """Deploy a vehicle at a specific location"""
    try:
        # Check if vehicle already exists
        existing_vehicle = get_vehicle(world, role_name)
        if existing_vehicle:
            logger.info(f"Vehicle with role name '{role_name}' already exists")
            spawned_actors.append(existing_vehicle)
            return existing_vehicle
        
        # Get vehicle blueprint
        vehicles = world.get_blueprint_library().filter("vehicle.*")
        if not vehicles:
            logger.error("No vehicle blueprints found")
            return None
        
        # Choose a suitable vehicle blueprint (always prefer Dodge Charger)
        blueprint = None
        for bp in vehicles:
            if "charger" in bp.id.lower():
                blueprint = bp
                logger.info(f"Selected Dodge Charger: {bp.id}")
                break
        if not blueprint:
            logger.warning("Dodge Charger not found, using fallback vehicle")
            blueprint = vehicles[0]  # Fallback
        
        blueprint.set_attribute("role_name", role_name)
        
        # Try to spawn at the specified location
        vehicle = world.try_spawn_actor(blueprint, transform)
        if not vehicle:
            # If exact location fails, try nearby locations
            for offset in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                offset_transform = carla.Transform(
                    carla.Location(
                        x=transform.location.x + offset[0],
                        y=transform.location.y + offset[1],
                        z=transform.location.z
                    ),
                    transform.rotation
                )
                vehicle = world.try_spawn_actor(blueprint, offset_transform)
                if vehicle:
                    break
        
        if not vehicle:
            logger.error(f"Failed to spawn vehicle {role_name} at specified location")
            return None
        
        logger.info(f"Deployed {role_name} vehicle: {vehicle.type_id} at {vehicle.get_transform().location}")
        spawned_actors.append(vehicle)
        return vehicle
        
    except Exception as e:
        logger.error(f"Error deploying vehicle {role_name}: {e}")
        return None

def deploy_vehicle(
    world: carla.World,
    role_name: str,
    autopilot: bool,
    spawned_actors: list[carla.Vehicle],
) -> carla.Vehicle | None:
    """Deploy a vehicle with the specified role name in the world"""

    if not world:
        logger.warning("World not set, cannot deploy vehicle")
        return None

    # Check if the vehicle already exists
    existing_vehicle = get_vehicle(world, role_name)
    if existing_vehicle:
        logger.info(
            f"Vehicle with role name '{role_name}' already exists: {existing_vehicle.type_id}"
        )
        spawned_actors.append(existing_vehicle)
        return existing_vehicle

    logger.info(f"Deploying vehicle with role name: {role_name}")

    # Get available vehicle blueprints
    vehicles = world.get_blueprint_library().filter("vehicle.*")
    if not vehicles:
        logger.warning("No vehicle blueprints found")
        return None

    # Choose a suitable vehicle blueprint (always prefer Dodge Charger)
    blueprint = None
    for bp in vehicles:
        if "charger" in bp.id.lower():
            blueprint = bp
            logger.info(f"Selected Dodge Charger: {bp.id}")
            break

    if not blueprint:
        logger.warning("Dodge Charger not found, using fallback vehicle")
        blueprint = vehicles[0]  # Fallback to first available

    logger.debug(f"Using vehicle blueprint: {blueprint.id}")
    blueprint.set_attribute("role_name", role_name)

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
                logger.error(
                    f"Failed to spawn vehicle {role_name} after {max_attempts} attempts"
                )
                raise e
            continue

    # Set autopilot if required
    if autopilot:
        vehicle.set_autopilot(True)
        logger.info(f"Vehicle {role_name} set to autopilot")

    logger.info(
        f"Deployed {role_name} vehicle: {vehicle.type_id} at {vehicle.get_transform().location}"
    )
    spawned_actors.append(vehicle)
    return vehicle
