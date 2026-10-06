#!/usr/bin/env python3
import argparse
import json
import socket
import sys

# ---- command-line argument parsing ----
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mini Discord TCP client")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="Server TCP port (default: 9000)")
    return parser.parse_args()

# --- response formatting ----
def format_response(raw_line: str) -> str:
    """
    Prints the raw JSON response line the server sent in
    readable format in the terminal instead of one dense line.
    If the response is not valid JSON for some reason, prints the raw text instead
    to avoid crashing the client.
    """
    try:
        obj = json.loads(raw_line)
    except json.JSONDecodeError:
        return raw_line
    return json.dumps(obj, indent=2)

def main() -> None:
    args = parse_args()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((args.host, args.port))
    except OSError as exc:
        print(f"Could not connect to {args.host}:{args.port} -- {exc}")
        sys.exit(1)

    """
    makefile -> Buffer incoming fragments, cut the stream into complete lines at each newline,
    then decode those lines correctly even if a unique character is split across fragments.
    The application code only sees complete, decoded command lines
    """
    reader = sock.makefile("r", encoding="utf-8", newline="\n")
    writer = sock.makefile("w", encoding="utf-8", newline="\n")

    # print welcome message and available commands
    print(f"Connected to Mini Discord at {args.host}:{args.port}")
    print("Commands: REGISTER <user>, LOGIN <user>, LOGOUT, JOIN <#channel>,")
    print("LEAVE <#channel>, SEND <#channel> <text>, LIST CHANNELS,")
    print("LIST USERS <#channel>, HISTORY <#channel> [limit], QUIT")
    print()

    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                # stdin closed (e.g. piped input ran out, or Ctrl+D)
                # treat like the user asked to quit.
                print()
                break

            if not line:
                continue

            try:
                writer.write(line + "\n")
                writer.flush()
            except OSError:
                print("Connection to server lost.")
                break

            response = reader.readline()
            if not response:
                # Empty read means the server closed the connection
                print("Server closed the connection.")
                break

            print(format_response(response.rstrip("\n")))

            if line.upper() == "QUIT":
                break
    finally:
        for stream in (reader, writer, sock):
            try:
                stream.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()