"""Explicit socket-owning development launcher; no public binding and no proxy trust."""
import argparse
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    from auth.local_dev import bind_loopback, enabled

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not enabled():
        parser.error("Set AXTRACT_LOCAL_NO_AUTH=true explicitly, or use the normal protected server command.")
    sock = bind_loopback(args.host, args.port)
    try:
        import uvicorn
        from main import app

        app.state.local_no_auth_socket = sock
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, proxy_headers=False))
        server.run(sockets=[sock])
    finally:
        sock.close()


if __name__ == "__main__":
    main()
