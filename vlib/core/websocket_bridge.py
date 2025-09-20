import asyncio
import json
import logging
import threading
import weakref
from typing import Optional, Dict, Any, Callable
import websockets
from websockets.server import WebSocketServerProtocol

from .sensors import V2XSensor, CAMData, v2x_sensors
import carla

logger = logging.getLogger(__name__)


class V2XWebSocketBridge:
    """WebSocket bridge that acts as a proxy for the player vehicle's V2X sensor"""
    
    def __init__(self, player_vehicle: carla.Vehicle, world: carla.World, port: int = 4000):
        self.player_vehicle = player_vehicle
        self.world = world
        self.port = port
        
        self.websocket: WebSocketServerProtocol | None = None
        self.server = None
        self.server_task = None
        self.loop = None
        
        self.server_thread: threading.Thread | None = None 
        self.player_sensor: V2XSensor | None = None
        self.original_message_handlers = []
        self.virtual_sensor: WebSocketVirtualSensor | None = None
        
    async def start_server(self):
        """Start the WebSocket server"""
        logger.info(f"Starting WebSocket server on port {self.port}")
        try:
            async def handler(websocket):
                path = getattr(websocket, 'path', '/')
                await self.handle_client(websocket, path)
                
            self.server = await websockets.serve(
                handler,
                "localhost",
                self.port,
                max_size=1024*1024,  # 1MB max message size
                ping_interval=20,
                ping_timeout=10
            )
            logger.info(f"WebSocket server started on ws://localhost:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}")
            
    def start_server_thread(self):
        """Start WebSocket server in a separate thread"""
        def run_server():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.server_task = self.loop.create_task(self.start_server())
            self.loop.run_forever()
            
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str = ""):
        """Handle WebSocket client connection"""
        if self.websocket is not None:
            logger.warning("Another client tried to connect, but only one connection is allowed")
            await websocket.close(code=1008, reason="Only one connection allowed")
            return
            
        self.websocket = websocket
        logger.info(f"WebSocket client connected from {websocket.remote_address}")
        
        try:
            # Find and setup player sensor proxy
            await self._setup_player_sensor_proxy()
            
            # Create virtual sensor for the WebSocket client
            self.virtual_sensor = WebSocketVirtualSensor(
                world=self.world,
                player_vehicle=self.player_vehicle,
                websocket_bridge=self
            )
            
            # Handle incoming messages
            async for message in websocket:
                await self._handle_incoming_message(message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Error handling WebSocket client: {e}")
            import traceback
            logger.debug(f"Full traceback: {traceback.format_exc()}")
        finally:
            await self._cleanup_client()
            
    async def _setup_player_sensor_proxy(self):
        """Find the player vehicle's sensor and set up message forwarding"""
        logger.debug(f"Looking for sensors attached to vehicle ID: {self.player_vehicle.id}")
        logger.debug(f"Total V2X sensors in registry: {len(v2x_sensors)}")
        
        for sensor in v2x_sensors:
            if (hasattr(sensor, 'attach_to') and sensor.attach_to and 
                sensor.attach_to.id == self.player_vehicle.id):
                self.player_sensor = sensor
                logger.info(f"Found player sensor: {sensor.sensor_id}")
                break
                
        if not self.player_sensor:
            logger.error("Could not find player vehicle sensor for WebSocket proxy!")
            return
            
        self.original_message_handlers = self.player_sensor.message_handlers.copy()
        self.player_sensor.add_message_handler(self._forward_to_websocket)
        logger.info(f"Added WebSocket message handler to sensor {self.player_sensor.sensor_id}")
        
    async def _handle_incoming_message(self, message: str):
        """Handle incoming CAM message from WebSocket client"""
        try:
            data = json.loads(message)
            
            if data.get("type") == "cam":
                # Convert JSON to CAMData
                cam_data = CAMData.from_dict(data["payload"])
                
                # Set sender_id to match player vehicle if not specified
                if not cam_data.sender_id or cam_data.sender_id == "websocket_client":
                    cam_data.sender_id = f"hero_{self.player_vehicle.id}"
                
                # Inject the message into the V2X system via virtual sensor
                if self.virtual_sensor:
                    self.virtual_sensor.inject_cam_message(cam_data)
                    logger.debug(f"Injected CAM message from WebSocket client: {cam_data.sender_id}")
                    
            elif data.get("type") == "ping":
                # Respond to ping
                await self._send_to_websocket({"type": "pong", "timestamp": data.get("timestamp")})
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from WebSocket client: {e}")
            await self._send_error("Invalid JSON format")
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")
            await self._send_error(f"Processing error: {str(e)}")
            
    def _forward_to_websocket(self, cam_data: CAMData):
        """Forward received CAM messages to WebSocket client"""
        if not self.websocket:
            logger.debug("No WebSocket client connected, skipping CAM forward")
            return
            
        logger.debug(f"Forwarding CAM from {cam_data.sender_id} to WebSocket client")
            
        try:
            message = {
                "type": "cam",
                "payload": cam_data.to_dict()
            }
            
            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._send_to_websocket(message), self.loop
                )
            else:
                logger.error("Event loop not running, cannot send to WebSocket")
                
        except Exception as e:
            logger.error(f"Error forwarding message to WebSocket: {e}")
            import traceback
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            # Clear websocket reference if there's an error
            self.websocket = None
                
    async def _send_to_websocket(self, message: Dict):
        """Send message to WebSocket client"""
        if self.websocket:
            try:
                await self.websocket.send(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending to WebSocket: {e}")
                self.websocket = None
                
    async def _send_error(self, error_msg: str):
        """Send error message to WebSocket client"""
        await self._send_to_websocket({
            "type": "error",
            "message": error_msg
        })
        
    async def _cleanup_client(self):
        """Clean up client connection"""
        self.websocket = None
        
        # Restore original message handlers
        if self.player_sensor and self.original_message_handlers:
            try:
                self.player_sensor.message_handlers = self.original_message_handlers
            except Exception as e:
                logger.error(f"Error restoring message handlers: {e}")
            
        # Remove virtual sensor
        if self.virtual_sensor:
            try:
                self.virtual_sensor.destroy()
                self.virtual_sensor = None
            except Exception as e:
                logger.error(f"Error destroying virtual sensor: {e}")
            
        logger.info("WebSocket client cleaned up")
        
    def stop(self):
        """Stop the WebSocket server"""
        if self.loop and self.loop.is_running():
            if self.server:
                self.loop.call_soon_threadsafe(self.server.close)
            self.loop.call_soon_threadsafe(self.loop.stop)
            
        if hasattr(self, 'server_thread') and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)
            
        logger.info("WebSocket server stopped")


class WebSocketVirtualSensor:
    """Virtual V2X sensor that represents the WebSocket client in the simulation"""
    
    def __init__(self, world: carla.World, player_vehicle: carla.Vehicle, websocket_bridge: V2XWebSocketBridge):
        self.world = world
        self.player_vehicle = player_vehicle
        self.websocket_bridge = websocket_bridge
        self.sensor_id = f"websocket_client_{player_vehicle.id}"
        
        # Inherit position from player vehicle
        self.location = None
        self.attach_to = player_vehicle
        
        # Add to global sensor registry
        v2x_sensors.append(self)
        logger.info(f"Virtual WebSocket sensor created: {self.sensor_id}")
        
    def inject_cam_message(self, cam_data: CAMData):
        """Inject CAM message into the V2X system as if sent from player vehicle"""
        # Update location to match player vehicle
        self._update_location()
        
        # Set sender location to player vehicle location for range calculations
        if self.location and cam_data.vehicle_data:
            cam_data.vehicle_data["position"] = self.location
        elif self.location:
            cam_data.vehicle_data = {"position": self.location}
            
        # Broadcast to other sensors using the same logic as regular sensors
        import math
        
        # Use default config values for range calculation
        max_path_loss = 140  # Default from V2XSensorConfig
        frequency_loss = 20 * math.log10(5900 * 1000) + 32.44  # 5.9 GHz default
        max_distance = min(
            10 ** ((max_path_loss - frequency_loss) / 20) * 1000,
            1000  # Default filter distance
        )
        max_distance = 30
        
        # Broadcast to all other V2X sensors within range
        for sensor in v2x_sensors:
            if (sensor.sensor_id != self.sensor_id and 
                hasattr(sensor, 'location') and sensor.location is not None):
                
                distance = math.sqrt(
                    (self.location.x - sensor.location.x) ** 2 +
                    (self.location.y - sensor.location.y) ** 2 +
                    (self.location.z - sensor.location.z) ** 2
                )
                
                if distance <= max_distance:
                    if hasattr(sensor, 'receive_cam'):
                        sensor.receive_cam(cam_data)
                        
    def _update_location(self):
        """Update location to match player vehicle"""
        if self.player_vehicle and hasattr(self.player_vehicle, 'get_location'):
            try:
                self.location = self.player_vehicle.get_location()
            except Exception:
                # Vehicle might be destroyed
                pass
                
    def receive_cam(self, cam_data: CAMData):
        """This virtual sensor doesn't receive messages directly (player sensor handles that)"""
        pass
        
    def destroy(self):
        """Remove virtual sensor from global registry"""
        if self in v2x_sensors:
            v2x_sensors.remove(self)
        logger.info(f"Virtual WebSocket sensor destroyed: {self.sensor_id}")
