import carla 
import logging
import random

logger = logging.getLogger(__name__)

def get_vehicle(world: carla.World , role_name: str) -> carla.Vehicle | None:
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

def deploy_vehicle(world, role_name: str, autopilot: bool) -> carla.Vehicle | None:
    """Deploy a vehicle with the specified role name in the world"""
    
    if not world:
        logger.warning("World not set, cannot deploy vehicle")
        return None

    # Check if the vehicle already exists
    
    existing_vehicle = get_vehicle(world, role_name)
    if existing_vehicle:
        logger.info(f"Vehicle with role name '{role_name}' already exists: {existing_vehicle.type_id}")
        return existing_vehicle
            
    print(f"Deploying vehicle with role name: {role_name}")
    # Spawn a vehicle with the specified role name
    vehicles = world.get_blueprint_library().filter('vehicle.*')
    if not vehicles:
        logger.warning("No vehicle blueprints found")
        return None
    
    blueprint = vehicles[0]
    logger.debug(f"Using vehicle blueprint: {blueprint.id}")

    if not blueprint:
        logger.warning(f"Vehicle blueprint for {role_name} not found")
        return None

    blueprint.set_attribute('role_name', role_name)
    
    spawn_points = world.get_map().get_spawn_points()
    
    # Try multiple spawn points to avoid collisions
    vehicle: carla.Vehicle | None = None
    max_attempts = 3
    
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
    if autopilot and vehicle: 
        vehicle.set_autopilot(True)
        logger.info(f"Vehicle {role_name} set to autopilot")

    logger.info(f"Deployed {role_name} vehicle: {vehicle.type_id} at {vehicle.get_transform().location}")
    return vehicle
