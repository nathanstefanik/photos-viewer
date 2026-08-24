"""Local runner (optional). Production uses uvicorn via Docker CMD."""

import uvicorn
from app.config import settings


def main():
    print(f"Immich: {settings.immich_url}")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
