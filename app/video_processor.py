"""Video processing pipeline for attendance monitoring."""

import cv2
import base64
import asyncio

import numpy as np

from app.detector import PersonDetector
from app.monitor import AttendanceMonitor


class VideoProcessor:
    """Processes video frames for person detection and attendance monitoring."""

    def __init__(self, fps: int = 5):
        """
        Initialize the video processor.

        Args:
            fps: Frames per second to process (lower = less CPU intensive)
        """
        self.detector = PersonDetector(model_name="yolo11s.pt")
        self.fps = fps

    async def process_video(
        self,
        video_path: str,
        on_update,
        monitor: AttendanceMonitor
    ) -> None:
        """
        Process video file and stream results via callback.

        Args:
            video_path: Path to video file
            on_update: Async callback function for sending updates
            monitor: AttendanceMonitor instance
        """
        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            await on_update({
                'type': 'error',
                'message': 'Failed to open video file'
            })
            return

        # Get video FPS to calculate frame skip
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if video_fps == 0:
            video_fps = 30  # Default if detection fails

        frame_skip = max(1, int(video_fps / self.fps))
        frame_count = 0

        print(f"Processing video: {total_frames} frames at {video_fps} FPS")
        print(f"Processing rate: {self.fps} FPS (skipping every {frame_skip} frames)")

        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_count += 1

                # Skip frames to achieve target FPS
                if frame_count % frame_skip != 0:
                    continue

                # Detect persons in frame
                detection = self.detector.detect_persons(frame)

                # Draw bounding boxes on frame copy
                display_frame = frame.copy()
                for box in detection['boxes']:
                    x1, y1, x2, y2 = box
                    # Draw green rectangle
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    # Add person label
                    cv2.putText(
                        display_frame,
                        'Person',
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        2
                    )

                # Add count overlay
                cv2.putText(
                    display_frame,
                    f"Count: {detection['count']}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (0, 255, 0),
                    2
                )

                # Encode frame to base64 JPEG
                _, buffer = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')

                # Send frame update
                await on_update({
                    'type': 'frame_update',
                    'frame': frame_b64,
                    'count': detection['count'],
                    'boxes': detection['boxes'],
                    'frame_number': frame_count,
                    'is_processing': True
                })

                # Check for alerts
                alert_result = monitor.update_count(detection['count'], frame)
                if alert_result['alert_triggered']:
                    await on_update({
                        'type': 'alert',
                        **alert_result['alert_data']
                    })

                # Control processing speed
                await asyncio.sleep(1.0 / self.fps)

        except Exception as e:
            print(f"Error processing video: {e}")
            await on_update({
                'type': 'error',
                'message': f'Error processing video: {str(e)}'
            })
        finally:
            cap.release()
            await on_update({
                'type': 'processing_complete',
                'total_frames': frame_count,
                'is_processing': False
            })
            print(f"Video processing complete: {frame_count} frames processed")
