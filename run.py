"""Run the Steel Estimator application."""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

# Ensure .NET runtime is in PATH for ACadSharp DWG reader
dotnet_path = os.path.expanduser("~/.dotnet")
if os.path.exists(dotnet_path):
    os.environ["PATH"] = f"{dotnet_path}:{os.environ.get('PATH', '')}"

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))

    uvicorn.run(
        "backend.app.main:app",
        host=host,
        port=port,
        reload=True,
    )
