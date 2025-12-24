"""Military Attendance Check - Main Entry Point."""

import uvicorn
from app.api import app


def main():
    """Run the FastAPI application."""
    print("Starting Military Attendance Check System...")
    print("Server will be available at: http://localhost:8000")
    print("Press Ctrl+C to stop")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )


if __name__ == "__main__":
    main()
