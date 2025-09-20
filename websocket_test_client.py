#!/usr/bin/env python3
"""
Simple WebSocket client to test V2X communication with the challenge.

This script connects to the WebSocket server and demonstrates:
1. Sending CAM messages to the simulation
2. Receiving CAM messages from the simulation
3. Basic ping/pong functionality

Usage:
    python utils/websocket_test_client.py
"""

import asyncio
import json
import logging
import websockets
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class V2XWebSocketClient:
    def __init__(self, uri="ws://localhost:4000"):
        self.uri = uri
        self.websocket = None
        
    async def connect(self):
        """Connect to the WebSocket server"""
        try:
            self.websocket = await websockets.connect(self.uri)
            logger.info(f"Connected to {self.uri}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def send_cam_message(self, sender_id="websocket_client", speed=25.0, heading=90.0):
        """Send a test CAM message"""
        if not self.websocket:
            logger.error("Not connected")
            return
            
        cam_message = {
            "type": "cam",
            "payload": {
                "sender_id": sender_id,
                "timestamp": datetime.now().isoformat(),
                "vehicle_data": {
                    "speed": speed,
                    "heading": heading,
                    "vehicle_role": "websocket_test"
                },
                "extensions": {
                    "test_message": True,
                    "client_info": "Python WebSocket Test Client"
                },
                "station_id": sender_id,
                "generation_delta_time": 0,
                "station_type": "passenger-car",
                "position": {"x": 100.0, "y": 200.0, "z": 1.0},
                "speed": speed,
                "yaw_rate": 0.0,
                "vehicle_role": "websocket_test",
                "path_history": []
            }
        }
        
        try:
            await self.websocket.send(json.dumps(cam_message))
            logger.info(f"Sent CAM message: {sender_id} at {speed:.1f} m/s, {heading}°")
        except Exception as e:
            logger.error(f"Failed to send CAM message: {e}")
    
    async def send_ping(self):
        """Send a ping message"""
        if not self.websocket:
            logger.error("Not connected")
            return
            
        ping_message = {
            "type": "ping",
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            await self.websocket.send(json.dumps(ping_message))
            logger.info("Sent ping")
        except Exception as e:
            logger.error(f"Failed to send ping: {e}")
    
    async def listen_for_messages(self):
        """Listen for incoming messages from the server"""
        if not self.websocket:
            logger.error("Not connected")
            return
            
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    
                    if msg_type == "cam":
                        payload = data["payload"]
                        logger.info(f"Received CAM: {payload}")
                    elif msg_type == "pong":
                        logger.info("Received pong")
                        
                    elif msg_type == "error":
                        logger.error(f"Server error: {data.get('message')}")
                        
                    else:
                        logger.info(f"Received message: {data}")
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON received: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed")
        except Exception as e:
            logger.error(f"Error listening for messages: {e}")
    
    async def disconnect(self):
        """Disconnect from the server"""
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected")


async def main():
    client = V2XWebSocketClient()
    
    if not await client.connect():
        return
    
    try:
        # Start listening for messages in background
        listen_task = asyncio.create_task(client.listen_for_messages())
        
        # Send some test messages
        logger.info("=== Testing V2X WebSocket Communication ===")
        
        # Send ping
        await client.send_ping()
        await asyncio.sleep(1)
        
        # Send test CAM messages with different parameters
        # await client.send_cam_message("test_vehicle_1", speed=30.0, heading=45.0)
        # await asyncio.sleep(2)
        #
        # await client.send_cam_message("test_vehicle_2", speed=15.0, heading=180.0)
        # await asyncio.sleep(2)
        #
        # await client.send_cam_message("emergency_vehicle", speed=50.0, heading=270.0)
        
        # Listen until interrupted
        logger.info("Listening for incoming messages (press Ctrl+C to stop)...")
        try:
            await listen_task
        except asyncio.CancelledError:
            pass
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
