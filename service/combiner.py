import torch
import asyncio
import json
import tracemalloc
import websockets
import logging
from imutils.video import VideoStream
from models import Database
from path import abs_path
from train import FaceTrainer
from socket_manager import WebSocketManager
from face_recognition import FaceRecognition
from alert_manager import AlertManager

tracemalloc.start()
logging.basicConfig(level=logging.DEBUG)


class MainStream:
    def __init__(self, root_dir, camera_urls):
        self.database = Database()
        self.websocket_manager = WebSocketManager()
        self.urls = camera_urls
        self.trainer = FaceTrainer(root_dir)
        self.index = self.trainer.index
        self.known_face_names = self.trainer.known_face_names
        self.face_model = self.trainer.face_model
        self.alert_manager = AlertManager(self.websocket_manager)
        self.face_recognition = FaceRecognition(self)
        self.processing_queue = asyncio.Queue()  # Queue for processing frames

    async def capture_and_send_frames(self, url):
        """ Captures frames and sends them to the processing queue. """
        cap = VideoStream(url).start()
        while True:
            frame = cap.read()
            if frame is not None:
                await self.processing_queue.put((frame, url))
            await asyncio.sleep(0.1)

    async def process_frames(self):
        """ Processes frames from all cameras. """
        while True:
            frame, url = await self.processing_queue.get()
            self.face_recognition.current_frame = frame
            faces = self.face_model.get(frame)
            tasks = [self.face_recognition.process_face_and_mood(face) for face in faces]
            results = await asyncio.gather(*tasks)
            for name, mood, _ in results:
                if name is not None:
                    await self.alert_manager.handle_alert(name, mood, url, frame=frame)

    async def reload_face_encodings_periodically(self):
        while True:
            print("Reloading face encodings...")
            self.index, self.known_face_names = self.trainer.load_face_encodings(self.trainer.root_dir)
            self.face_recognition.index, self.face_recognition.known_face_names = self.trainer.load_face_encodings(
                self.trainer.root_dir)
            print("Length of face encodings: ", len(self.face_recognition.known_face_names))
            await self.update_camera_streams()
            await asyncio.sleep(600)

    async def start_camera_streams(self):
        """Start frame capture tasks for all cameras and the central processing task."""
        tasks = [asyncio.create_task(self.capture_and_send_frames(url)) for url in self.urls]
        tasks.append(asyncio.create_task(self.process_frames()))
        await asyncio.gather(*tasks)

    async def update_camera_streams(self):
        new_urls = self.database.get_camera_urls()
        added_urls = set(new_urls) - set(self.urls)

        if added_urls:
            print(f"New cameras added: {added_urls}")
            for url in added_urls:
                asyncio.create_task(self.capture_and_send_frames(url))
            self.urls.extend(added_urls)


async def websocket_server(websocket, path):
    manager = stream.websocket_manager
    try:
        message = await websocket.recv()
        data = json.loads(message) or None
        await manager.register(websocket, data)

        while True:
            await asyncio.sleep(10)

    except websockets.exceptions.ConnectionClosedError:
        await manager.unregister(websocket)
    except json.JSONDecodeError:
        await manager.unregister(websocket)
    finally:
        await manager.unregister(websocket)


async def main():
    ws_server = await websockets.serve(websocket_server, "0.0.0.0", 5000)
    camera_streams_task = asyncio.create_task(stream.start_camera_streams())
    reload_encodings_task = asyncio.create_task(stream.reload_face_encodings_periodically())
    await asyncio.gather(
        ws_server.wait_closed(),
        camera_streams_task,
        reload_encodings_task,  # Include the new task here
    )


if __name__ == "__main__":
    database = Database()
    urls = database.get_camera_urls()
    stream = MainStream(abs_path() + "media/employees", urls)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
