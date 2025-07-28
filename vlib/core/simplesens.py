import json
import time
from queue import Queue, Empty
from collections import defaultdict
import carla
import logging

logger = logging.getLogger(__name__)

class CommunicationHub:
    """Central hub for routing messages and monitoring heartbeats"""
    
    def __init__(self, world):
        self.world = world
        self.sensors = {}  # actor_id -> CommSensor
        self.message_log = []
        self.broadcast_channels = defaultdict(set)  # channel_name -> set of actor_ids
        self.statistics = CommunicationStats()
        logger.debug("Initialized Communication Hub")
        
    def register_sensor(self, actor_id, sensor):
        """Register a new communication sensor"""
        self.sensors[actor_id] = sensor
        self.statistics.add_actor(actor_id)
        logger.info(f"Communication Hub: Registered sensor for actor {actor_id}")
        
    def unregister_sensor(self, actor_id):
        """Remove sensor from hub"""
        if actor_id in self.sensors:
            del self.sensors[actor_id]
            self.statistics.remove_actor(actor_id)
            # TODO(sfx): Delete from the world as well?
            logger.info(f"Communication Hub: Unregistered sensor for actor {actor_id}")
            
    def route_message(self, sender_id, message):
        """Route message to appropriate recipients"""

        logger.info(f"Routing message from {sender_id}")
        message_type = message.get("message_type")
            
        target_ids = message.get("target_ids")
        broadcast_range = message.get("broadcast_range")
        
        json_message = json.dumps(message)
        recipients = set()
        
        # Direct targeting only when we know who to 
        # send the message to.
        if target_ids:
            recipients.update(target_ids)
            
        # Broadcast within range
        elif broadcast_range:
            recipients.update(self._get_actors_in_range(sender_id, broadcast_range))
        # Do we need to keep the above mutually exclusive?    
        
        else:
            recipients.update(self.sensors.keys())
            
        # Remove sender from recipients
        recipients.discard(sender_id)
        
        # Deliver to recipients who are subscribed to this message type
        delivered_count = 0
        for recipient_id in recipients:
            if recipient_id in self.sensors:
                sensor = self.sensors[recipient_id]
                if not sensor.subscriptions or message_type in sensor.subscriptions:
                    sensor.deliver_message(json_message)
                    delivered_count += 1
                    
        # Log message and update statistics
        self.message_log.append({
            "sender": sender_id,
            "recipients": list(recipients),
            "message_type": message_type,
            "delivered_count": delivered_count,
            "delivered_at": time.time()
        })
        
        self.statistics.record_message(sender_id, message_type, delivered_count)
        
    def _get_actors_in_range(self, sender_id, broadcast_range):
        """Get all actor IDs within broadcast range"""
        if sender_id not in self.sensors:
            return []
            
        try:
            # Get sender's actor object
            sender_actor = None
            for actor in self.world.get_actors():
                if actor.id == sender_id:
                    sender_actor = actor
                    break
                    
            if not sender_actor:
                return []
                
            sender_location = sender_actor.get_location()
            nearby_actors = []
            
            for actor in self.world.get_actors():
                if actor.id != sender_id and actor.id in self.sensors:
                    distance = sender_location.distance(actor.get_location())
                    if distance <= broadcast_range:
                        nearby_actors.append(actor.id)
                        
            return nearby_actors
            
        except Exception as e:
            print(f"Error getting actors in range: {e}")
            return []
            
    def create_channel(self, channel_name, actor_ids):
        """Create a broadcast channel for specific actors"""
        self.broadcast_channels[channel_name].update(actor_ids)
        
    def broadcast_to_channel(self, channel_name, sender_id, message_type, data):
        """Broadcast message to all actors in a channel"""
        if channel_name in self.broadcast_channels:
            target_ids = list(self.broadcast_channels[channel_name])
            message = {
                "sender_id": sender_id,
                "message_type": message_type,
                "data": data,
                "timestamp": time.time(),
                "channel": channel_name,
                "target_ids": target_ids
            }
            self.route_message(sender_id, message)
            
    def get_network_status(self):
        """Get comprehensive network status"""

        
        return {
            "total_sensors": len(self.sensors),
            "total_messages": self.statistics.total_messages,
            "messages_per_second": self.statistics.get_message_rate(),
            "channel_count": len(self.broadcast_channels)
        }

class CommunicationStats:
    """Track communication statistics"""
    
    def __init__(self):
        self.total_messages = 0
        self.messages_by_type = defaultdict(int)
        self.messages_by_actor = defaultdict(int)
        self.heartbeats_received = defaultdict(int)
        self.start_time = time.time()
        
    def add_actor(self, actor_id):
        """Add new actor to statistics tracking"""
        self.messages_by_actor[actor_id] = 0
        self.heartbeats_received[actor_id] = 0
        
    def remove_actor(self, actor_id):
        """Remove actor from statistics tracking"""
        self.messages_by_actor.pop(actor_id, None)
        self.heartbeats_received.pop(actor_id, None)
        
    def record_message(self, sender_id, message_type, recipient_count):
        """Record a message being sent"""
        self.total_messages += 1
        self.messages_by_type[message_type] += 1
        self.messages_by_actor[sender_id] += 1
        
    def record_heartbeat(self, actor_id):
        """Record a heartbeat being received"""
        self.heartbeats_received[actor_id] += 1
        
    def get_message_rate(self):
        """Get messages per second rate"""
        elapsed_time = time.time() - self.start_time
        if elapsed_time > 0:
            return self.total_messages / elapsed_time
        return 0
        
    def get_stats_summary(self):
        """Get comprehensive statistics summary"""
        return {
            "total_messages": self.total_messages,
            "message_rate": self.get_message_rate(),
            "messages_by_type": dict(self.messages_by_type),
            "messages_by_actor": dict(self.messages_by_actor),
            "total_heartbeats": sum(self.heartbeats_received.values()),
            "heartbeats_by_actor": dict(self.heartbeats_received)
        }


class SensorManager:
    """Manager for deploying communication sensors to all objects"""
    
    def __init__(self, world):
        self.world = world
        self.comm_hub = CommunicationHub(world)
        self.sensors = {}
        
    def deploy_to_all_actors(self):
        """Deploy communication sensors to all relevant actors"""
        actors = self.world.get_actors()
        
        for actor in actors:
            if self._should_have_sensor(actor):
                self.deploy_to_actor(actor.id)
                
    def deploy_to_actor(self, actor_id):
        """Deploy communication sensor to specific actor"""
        if actor_id not in self.sensors:
            sensor = CommSensor(actor_id, self.world, self.comm_hub)
            self.sensors[actor_id] = sensor
            
            # Set up default subscriptions based on actor type
            actor = self._get_actor_by_id(actor_id)
            if actor:
                self._setup_default_subscriptions(sensor, actor)
                
        return self.sensors.get(actor_id)
        
    def _should_have_sensor(self, actor):
        """Determine if actor should have a communication sensor"""
        return (actor.type_id.startswith('vehicle.') or 
                actor.type_id.startswith('walker.') or
                actor.type_id.startswith('traffic.'))
                
    def _get_actor_by_id(self, actor_id):
        """Get actor object by ID"""
        for actor in self.world.get_actors():
            if actor.id == actor_id:
                return actor
        return None
        
    def _setup_default_subscriptions(self, sensor, actor):
        """Setup default message subscriptions based on actor type"""

        # TODO(sfx): Do we really need heartbeats
        sensor.subscribe_to_type("heartbeat")

        # TODO(sfx): Move this into an enum for better management.
        if 'vehicle' in actor.type_id:
            sensor.subscribe_to_type("traffic_info")
            sensor.subscribe_to_type("collision_warning")
            sensor.subscribe_to_type("route_update")
            sensor.subscribe_to_type("vehicle_status")
            sensor.subscribe_to_type("emergency_alert")
            
        elif 'walker' in actor.type_id:
            sensor.subscribe_to_type("pedestrian_alert")
            sensor.subscribe_to_type("traffic_light_status")
            sensor.subscribe_to_type("safety_warning")
            sensor.subscribe_to_type("emergency_alert")
            
        elif 'traffic_light' in actor.type_id:
            sensor.subscribe_to_type("traffic_control")
            sensor.subscribe_to_type("emergency_override")
            sensor.subscribe_to_type("priority_request")
            
    def process_all_sensors(self):
        """Process messages for all sensors and handle cleanup"""
        for sensor in self.sensors.values():
            sensor.process_messages()

        #TODO(sfx): We need to have a health check cleanup here. 
        
    def get_network_status(self):
        """Get comprehensive network status"""
        return self.comm_hub.get_network_status()
        
    def print_status_report(self):
        """Print detailed status report"""
        status = self.get_network_status()
        stats = self.comm_hub.statistics.get_stats_summary()
        
        print("\n" + "="*50)
        print("COMMUNICATION NETWORK STATUS REPORT")
        print("="*50)
        print(f"Total Sensors: {status['total_sensors']}")
        print(f"Total Messages: {status['total_messages']}")
        print(f"Message Rate: {status['messages_per_second']:.2f} msg/sec")
        print(f"Channels: {status['channel_count']}")
        print("="*50)

class CommSensor:
    """
    A simple V2x sensor for data exchange between CARLA objects. 
    This sensor will be deployed for all objects inside the simulation world 
    and primarily sends JSON messages of an arbitraty type(for now).
    """
    
    def __init__(self, actor_id, world, comm_hub):
        # The object inside the simulation that this sensor belongs to.
        self.actor_id = actor_id
        # carla World 
        self.world = world
        # Communication Hub inside the simulation. This is effectively the
        # emulation for the physical network inside the simulator.
        self.comm_hub = comm_hub
        # We use inbox and outbox Queues for sending and receiving the messages
        self.inbox = Queue()
        self.outbox = Queue()
        # The simulation entities can subscribe to certain kinds of messages.
        self.subscriptions = set()
        self.message_handlers = {}
        self.is_active = True
        self.last_heartbeat = time.time()
        self.heartbeat_interval = 1.0  # Send heartbeat every second
        
        # Register with communication hub 
        self.comm_hub.register_sensor(self.actor_id, self)
        
    def send_message(self, message_type, data, target_ids=None, broadcast_range=None):
        """Send message to specific targets or broadcast within range"""
        message = {
            "sender_id": self.actor_id,
            "message_type": message_type,
            "data": data,
            "timestamp": time.time(),
            "target_ids": target_ids,
            "broadcast_range": broadcast_range
        }
        
        json_message = json.dumps(message)
        self.outbox.put(json_message)
        
        # Send to communication hub for routing
        self.comm_hub.route_message(self.actor_id, message)
        
    def send_heartbeat(self):
        """Send heartbeat to communication hub"""
        try:
            actor = self._get_actor()
            if actor:
                location = actor.get_location()
                velocity = actor.get_velocity()
                
                heartbeat_data = {
                    "actor_id": self.actor_id,
                    "actor_type": actor.type_id,
                    "location": {"x": location.x, "y": location.y, "z": location.z},
                    "velocity": {"x": velocity.x, "y": velocity.y, "z": velocity.z},
                    "health_status": "alive",
                    "active_subscriptions": list(self.subscriptions),
                    "message_queue_size": self.inbox.qsize()
                }
            else:
                heartbeat_data = {
                    "actor_id": self.actor_id,
                    "health_status": "actor_not_found",
                    "active_subscriptions": list(self.subscriptions),
                    "message_queue_size": self.inbox.qsize()
                }
                
            self.send_message("heartbeat", heartbeat_data)
            self.last_heartbeat = time.time()
            
        except Exception as e:
            print(f"Actor {self.actor_id}: Failed to send heartbeat - {e}")
            
    def _get_actor(self):
        """Get the actor object associated with this sensor"""
        for actor in self.world.get_actors():
            if actor.id == self.actor_id:
                return actor
        return None
        
    def should_send_heartbeat(self):
        """Check if it's time to send a heartbeat"""
        return (time.time() - self.last_heartbeat) >= self.heartbeat_interval
        
    def receive_message(self, timeout=0.1):
        """Receive message from inbox"""
        try:
            json_message = self.inbox.get(timeout=timeout)
            return json.loads(json_message)
        except Empty:
            return None
        except json.JSONDecodeError:
            print(f"Actor {self.actor_id}: Invalid JSON received")
            return None
            
    def subscribe_to_type(self, message_type):
        """Subscribe to specific message types"""
        self.subscriptions.add(message_type)
        
    def unsubscribe_from_type(self, message_type):
        """Unsubscribe from message type"""
        self.subscriptions.discard(message_type)
        
    def register_handler(self, message_type, handler_function):
        """Register callback function for specific message types"""
        self.message_handlers[message_type] = handler_function
        
    def deliver_message(self, json_message):
        """Called by communication hub to deliver message"""
        self.inbox.put(json_message)
        
        # Auto-handle if handler is registered
        try:
            message = json.loads(json_message)
            message_type = message.get("message_type")
            if message_type in self.message_handlers:
                self.message_handlers[message_type](message)
        except json.JSONDecodeError:
            pass
            
    def process_messages(self):
        """Process all pending messages and send heartbeat if needed"""
        messages = []
        
        # Process incoming messages
        while True:
            message = self.receive_message(timeout=0.01)
            if message is None:
                break
            messages.append(message)
            
        # Send heartbeat if needed
        # TODO(sfx): Handle hearbeats as normal messages.
        if self.should_send_heartbeat():
            self.send_heartbeat()
            
        return messages
        
    def destroy(self):
        """Clean up sensor"""
        self.is_active = False
        self.comm_hub.unregister_sensor(self.actor_id)




