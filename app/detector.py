"""Person detection module using YOLO v8."""

from ultralytics import YOLO
import numpy as np


class PersonDetector:
    """Detects persons in video frames using YOLO v8."""

    def __init__(self, model_name: str = 'yolov8n.pt'):
        """
        Initialize the person detector.

        Args:
            model_name: YOLO model to use (default: yolov8n.pt for speed)
        """
        self.model = YOLO(model_name)
        # COCO dataset class 0 is 'person'
        self.person_class_id = 0

    def detect_persons(self, frame: np.ndarray) -> dict:
        """
        Detect persons in a video frame.

        Args:
            frame: Video frame as numpy array (BGR format from OpenCV)

        Returns:
            Dictionary with:
                - count: Number of persons detected
                - boxes: List of bounding boxes as (x1, y1, x2, y2) tuples
                - confidences: List of confidence scores
        """
        # Run YOLO inference with person class filter
        results = self.model(frame, classes=[self.person_class_id], conf=0.35, imgsz=1280, verbose=False)

        boxes = []
        confidences = []

        # Extract bounding boxes and confidences
        for result in results:
            if result.boxes is not None:
                for box in result.boxes:
                    # Get coordinates
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    # Get confidence score
                    conf = box.conf[0].item()

                    boxes.append((int(x1), int(y1), int(x2), int(y2)))
                    confidences.append(conf)

        return {
            'count': len(boxes),
            'boxes': boxes,
            'confidences': confidences
        }
