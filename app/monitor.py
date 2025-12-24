"""Attendance monitoring and alert logic."""

from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
import json
import os


class AttendanceMonitor:
    """Monitors person count and triggers alerts when attendance drops."""

    ALERT_THRESHOLD_SECONDS = 30

    def __init__(self, alerts_dir: str = 'alerts'):
        """
        Initialize the attendance monitor.

        Args:
            alerts_dir: Directory to save alert frames
        """
        self.baseline_count = None
        self.current_count = 0
        self.below_baseline_start = None
        self.alerts_dir = Path(alerts_dir)
        self.alerts_dir.mkdir(exist_ok=True)
        self.alerts_dir.mkdir(exist_ok=True)
        self.recent_alerts = []
        
        # Load alerts from disk
        self.alerts_file = self.alerts_dir / "alerts.json"
        if self.alerts_file.exists():
            try:
                with open(self.alerts_file, "r") as f:
                    self.recent_alerts = json.load(f)
            except Exception as e:
                print(f"Error loading alerts: {e}")

    def set_baseline(self, count: int) -> None:
        """
        Set the baseline person count.

        Args:
            count: Number of persons to use as baseline
        """
        self.baseline_count = count
        self.below_baseline_start = None
        print(f"Baseline set to {count} persons")

    def update_count(self, count: int, frame: np.ndarray) -> dict:
        """
        Update current count and check for alert conditions.

        Args:
            count: Current number of persons detected
            frame: Current video frame

        Returns:
            Dictionary with:
                - alert_triggered: bool
                - alert_data: dict or None (timestamp, image_path, message)
        """
        self.current_count = count

        # Can't trigger alerts without baseline
        if self.baseline_count is None:
            return {'alert_triggered': False, 'alert_data': None}

        # Check if count is below baseline
        if count < self.baseline_count:
            # Start timer if not already started
            if self.below_baseline_start is None:
                self.below_baseline_start = datetime.now()
                print(f"Count dropped below baseline: {count} < {self.baseline_count}")

            # Check if threshold exceeded
            elapsed = (datetime.now() - self.below_baseline_start).total_seconds()
            print(elapsed)
            if elapsed >= self.ALERT_THRESHOLD_SECONDS:
                # Trigger alert
                alert_data = self._trigger_alert(frame, count)
                # Reset timer to avoid repeated alerts
                self.below_baseline_start = None
                return {'alert_triggered': True, 'alert_data': alert_data}
        else:
            # Count recovered, reset timer
            if self.below_baseline_start is not None:
                print(f"Count recovered: {count} >= {self.baseline_count}")
            self.below_baseline_start = None

        return {'alert_triggered': False, 'alert_data': None}

    def _trigger_alert(self, frame: np.ndarray, count: int) -> dict:
        """
        Trigger an alert and save the frame.

        Args:
            frame: Video frame to save
            count: Current person count

        Returns:
            Alert data dictionary
        """
        timestamp = datetime.now()
        filename = f"alert_{timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
        filepath = self.alerts_dir / filename

        # Save the frame
        cv2.imwrite(str(filepath), frame)

        message = f"Attendance dropped to {count} (baseline: {self.baseline_count})"
        print(f"ALERT: {message}")

        alert_data = {
            'timestamp': timestamp.isoformat(),
            'image_path': str(filepath),
            'message': message,
            'count': count,
            'baseline': self.baseline_count
        }
        
        self.recent_alerts.append(alert_data)
        
        # Save to disk
        try:
            with open(self.alerts_file, "w") as f:
                json.dump(self.recent_alerts, f, indent=2)
        except Exception as e:
            print(f"Error saving alerts: {e}")
        
        return alert_data

    def get_status(self) -> dict:
        """
        Get current monitoring status.

        Returns:
            Status dictionary with baseline, current count, and time below baseline
        """
        time_below = None
        if self.below_baseline_start is not None:
            time_below = (datetime.now() - self.below_baseline_start).total_seconds()

        return {
            'baseline_count': self.baseline_count,
            'current_count': self.current_count,
            'time_below_baseline': time_below
        }

    def get_recent_alerts(self, limit: int = 50) -> list:
        """
        Get list of recent alerts.

        Args:
            limit: Maximum number of alerts to return

        Returns:
            List of alert dictionaries
        """
        # Sort by timestamp descending
        return sorted(self.recent_alerts, key=lambda x: x['timestamp'], reverse=True)[:limit]
