import argparse
import uvicorn
from .web import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the local hand-built Agent UI")
    parser.add_argument("--port",type=int,default=8000)
    args = parser.parse_args()
    # Single worker is intentional: session actor locks live in this process.
    uvicorn.run(create_app(),host="127.0.0.1",port=args.port,workers=1)


if __name__ == "__main__":
    main()
