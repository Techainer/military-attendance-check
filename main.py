"""Military Attendance Check - Main Entry Point."""

import os

import uvicorn
from app.api import app


def main():
    """Run the FastAPI application."""
    from app import hw
    print("Starting Military Attendance Check System...")
    print("AI:", hw.describe())
    port = int(os.environ.get("HORUS_PORT", "8199"))
    print(f"Server will be available at: http://localhost:{port}")
    print("Press Ctrl+C to stop")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
