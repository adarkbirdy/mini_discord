"""
Mini Discord, PA1
John Kim, CS 249
"""

import json
import re
import threading
import time
import socket
import argparse
from dataclasses import dataclass, field
from typing import Optional
from flask import Flask, jsonify, request


# ---- validation regexes ----
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CHANNEL_RE = re.compile(r"^#[A-Za-z0-9_-]+$")

def is_valid_username(username: str) -> bool:
    return bool(username) and bool(USERNAME_RE.match(username))

def is_valid_channel(channel: str) -> bool:
    return bool(channel) and bool(CHANNEL_RE.match(channel))


# ---- HTTP and TCP layers both catch errors and provides readable messages ----
class AppError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---- data classes ----
@dataclass
class User:
    # only contains a single string field but if we need more user-related data later than it could be useful
    # also looks more consistent this way with the other data classes
    username: str

@dataclass
class Channel:
    name: str
    members: set = field(default_factory=set)  # set of usernames since member lookup is fast in a set

@dataclass
class Message:
    message_id: int
    channel: str
    username: str
    text: str
    # timestamp to record exact time and order of messages sent; maybe useful for future update
    # default_factory=time.time ensures that the timestamp is set to the current time when the message is created, not at the time of class definition
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "username": self.username,
            "text": self.text,
            "timestamp": self.timestamp,
        }


# ---- shared, thread-safe application state ----
class SharedState:

    def __init__(self):
        # containers for everything the server needs to hold information-wise
        self._users: dict[str, User] = {}
        self._channels: dict[str, Channel] = {}
        self._messages: list[Message] = []
        self._next_message_id = 1

        # a reentrant lock; necessary over a regular thread lock because we don't want a thread
        # to deadlock itself by requiring a lock it already has
        self._lock = threading.RLock()


    # ---- users ----
    def create_user(self, username: str) -> User:
        if not is_valid_username(username):
            raise AppError("INVALID_USERNAME", f"Invalid username: {username!r}") # !r adds quotes around username for easy identification
        with self._lock: # lock is released at end of the with block
            if username in self._users:
                raise AppError("CONFLICT", f"User already exists: {username!r}")
            user = User(username=username)
            self._users[username] = user
            return user

    def user_exists(self, username: str) -> bool:
        with self._lock:
            return username in self._users

    def require_user(self, username: str) -> User:
        with self._lock:
            user = self._users.get(username)
        if user is None:
            raise AppError("NOT_FOUND", f"Unknown user: {username!r}")
        return user

    def list_users(self) -> list[str]:
        with self._lock:
            return sorted(self._users.keys())


    # ---- channels ----
    def _get_or_create_channel(self, channel: str) -> Channel:
        chan = self._channels.get(channel)
        if chan is None:
            chan = Channel(name=channel)
            self._channels[channel] = chan
        return chan

    def list_channels(self) -> list[str]:
        with self._lock:
            return sorted(self._channels.keys())

    def join_channel(self, channel: str, username: str) -> None:
        if not is_valid_channel(channel):
            raise AppError("INVALID_CHANNEL", f"Invalid channel: {channel!r}")
        with self._lock:
            self.require_user(username)
            chan = self._get_or_create_channel(channel)
            chan.members.add(username)

    def leave_channel(self, channel: str, username: str) -> None:
        with self._lock:
            chan = self._channels.get(channel)
            if chan is None:
                raise AppError("NOT_FOUND", f"Unknown channel: {channel!r}")
            chan.members.discard(username)

    def list_channel_members(self, channel: str) -> list[str]:
        with self._lock:
            chan = self._channels.get(channel)
            if chan is None:
                raise AppError("NOT_FOUND", f"Unknown channel: {channel!r}")
            return sorted(chan.members)

    def is_member(self, channel: str, username: str) -> bool:
        with self._lock:
            chan = self._channels.get(channel)
            return chan is not None and username in chan.members

    def remove_user_from_all_channels(self, username: str) -> None:
        # TCP failure detection to clean up memberships
        with self._lock:
            for chan in self._channels.values():
                chan.members.discard(username)


    # ---- messages ----
    def send_message(self, channel: str, username: str, text: str) -> Message:
        if not text:
            raise AppError("BAD_REQUEST", "Message text must not be empty")
        with self._lock:
            chan = self._channels.get(channel)
            if chan is None:
                raise AppError("NOT_FOUND", f"Unknown channel: {channel!r}")
            if username not in chan.members:
                raise AppError(
                    "BAD_REQUEST",
                    f"User {username!r} must join {channel!r} before sending messages",
                )
            msg = Message(
                message_id=self._next_message_id,
                channel=channel,
                username=username,
                text=text,
            )
            self._next_message_id += 1 # importantly inside the lock to avoid conflicts with duplicate message IDs
            self._messages.append(msg)
            return msg

    def get_history(self, channel: str, limit: Optional[int] = None) -> list[Message]:
        with self._lock:
            if channel not in self._channels:
                raise AppError("NOT_FOUND", f"Unknown channel: {channel!r}")
            msgs = [m for m in self._messages if m.channel == channel]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs

# end of shared state class

ERROR_CODES = {
    "BAD_REQUEST": 400,
    "NOT_AUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INVALID_USERNAME": 400,
    "INVALID_CHANNEL": 400,
    "SERVER_ERROR": 500,
}

"""
Flask app creation for a given SharedState instance.
It is a separate function that passes the SharedState instance to the Flask app
so that we can test multiple instances of the app with different SharedState objects cleanly and neatly.
"""
def create_app(state: SharedState) -> Flask:
    app = Flask(__name__)

    @app.errorhandler(AppError)
    def handle_app_error(err: AppError):
        status = ERROR_CODES.get(err.code, 400)
        return jsonify(status="error", code=err.code, message=err.message), status

    @app.errorhandler(404)
    def handle_404(err):
        return jsonify(status="error", code="NOT_FOUND", message="Not found"), 404

    """
    not listed in the above error codes, but we need it to handle an error Flask
    would raise and ensure it is jsonified for consistency's sake
    """
    @app.errorhandler(405)
    def handle_405(err):
        return jsonify(status="error", code="BAD_REQUEST", message="Unsupported HTTP method"), 405

    @app.errorhandler(500)
    def handle_500(err):
        return jsonify(status="error", code="SERVER_ERROR", message="Internal server error"), 500


    # ---- Parses the JSON body and returns field_name, or raises BAD_REQUEST ----
    def require_json(field_name: str):
        data = request.get_json(silent=True) # silent=True returns None instead of raising an error if the body is not valid JSON
        if not isinstance(data, dict):
            raise AppError("BAD_REQUEST", "Request body must be JSON")
        if field_name not in data or not isinstance(data[field_name], str):
            raise AppError("BAD_REQUEST", f"Missing or invalid field: {field_name}")
        return data[field_name]

    def require_x_user() -> str:
        username = request.headers.get("X-User")
        if not username:
            raise AppError("BAD_REQUEST", "Missing X-User header")
        state.require_user(username)  # raises NOT_FOUND if unknown
        return username

    # --- HTTP endpoint for health check ----
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(status="ok"), 200


    # ---- HTTP endpoints for user management ----
    @app.route("/users", methods=["POST"])
    def http_create_user():
        username = require_json("username")
        user = state.create_user(username)
        return jsonify(username=user.username), 201

    @app.route("/users", methods=["GET"])
    def http_list_users():
        return jsonify(users=state.list_users()), 200


    # ---- HTTP endpoints for channel management ----
    @app.route("/channels", methods=["GET"])
    def http_list_channels():
        return jsonify(channels=state.list_channels()), 200

    # <channel> is a path parameter, so it can be any string that matches the regex for a channel name since this is Flask
    @app.route("/channels/<channel>/members", methods=["POST"])
    def http_join_channel(channel):
        username = require_x_user()
        state.join_channel(channel, username)
        return jsonify(channel=channel, username=username, joined=True), 200

    @app.route("/channels/<channel>/users", methods=["GET"])
    def http_list_channel_members(channel):
        members = state.list_channel_members(channel)
        return jsonify(channel=channel, users=members), 200

    @app.route("/channels/<channel>/members/<username>", methods=["DELETE"])
    def http_leave_channel(channel, username):
        state.require_user(username)
        state.leave_channel(channel, username)
        return jsonify(channel=channel, username=username, left=True), 200


    # ---- HTTP endpoints for message management ----
    @app.route("/channels/<channel>/messages", methods=["POST"])
    def http_send_message(channel):
        username = require_x_user()
        text = require_json("text")
        msg = state.send_message(channel, username, text)
        return jsonify(message_id=msg.message_id, channel=channel, username=username, text=msg.text), 201

    @app.route("/channels/<channel>/messages", methods=["GET"])
    def http_get_history(channel):
        limit_raw = request.args.get("limit")
        limit = None
        if limit_raw is not None:
            if not limit_raw.isdigit() or int(limit_raw) <= 0:
                raise AppError("BAD_REQUEST", "limit must be a positive integer")
            limit = int(limit_raw)
        msgs = state.get_history(channel, limit)
        return jsonify(channel=channel, messages=[m.to_dict() for m in msgs]), 200

    return app


class TCPClientHandler:
    """
    Handles one TCP client connection end-to-end.
    Reads line-oriented commands, dispatches them against the shared SharedState,
    and writes back one JSON response per line.
    One instance runs per client thread so a client only blocks its own
    thread and not the rest of the server if it is slow or delayed in any way.
    """
    def __init__(self, conn: socket.socket, addr, state: SharedState):
        # socket set-up
        self.conn = conn
        self.addr = addr
        self.state = state
        self.current_user: Optional[str] = None
        """
        makefile -> Buffer incoming fragments, cut the stream into complete lines at each newline,
        then decode those lines correctly even if a unique character is split across fragments.
        The application code only sees complete, decoded command lines
        """
        self.reader = conn.makefile("r", encoding="utf-8", newline="\n")
        self.writer = conn.makefile("w", encoding="utf-8", newline="\n")

    def run(self):
        try:
            for raw_line in self.reader:
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                if not self.handle_line(line):
                    break  # QUIT was received; close normally
        except Exception:
            # Any read/decode error is treated the same as an abrupt
            # disconnect: clean up and move on, never crash the server
            pass
        finally:
            self._on_disconnect() # disconnect even on failure or abrupt disconnect

    # ---- failure-detection cleanup ----
    def _on_disconnect(self):
        # log the user out and remove them from every channel they were in
        if self.current_user is not None:
            self.state.remove_user_from_all_channels(self.current_user)
            self.current_user = None
        for stream in (self.reader, self.writer, self.conn):
            try:
                stream.close()
            except OSError:
                pass

    def send(self, obj: dict):
        self.writer.write(json.dumps(obj) + "\n")
        self.writer.flush()

    def send_error(self, code: str, message: str):
        self.send({"status": "error", "code": code, "message": message})

    def require_login(self) -> str:
        if self.current_user is None:
            raise AppError("NOT_AUTHENTICATED", "Please login first")
        return self.current_user

    def handle_line(self, line: str) -> bool:
        # Parses and dispatches one command line; return False on QUIT
        parts = line.split(maxsplit=1)
        command = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        # example of command and rest: REGISTER (command) alice (rest)

        try:
            if command == "REGISTER":
                self._cmd_register(rest)
            elif command == "LOGIN":
                self._cmd_login(rest)
            elif command == "LOGOUT":
                self._cmd_logout()
            elif command == "JOIN":
                self._cmd_join(rest)
            elif command == "LEAVE":
                self._cmd_leave(rest)
            elif command == "SEND":
                self._cmd_send(rest)
            elif command == "LIST":
                self._cmd_list(rest)
            elif command == "HISTORY":
                self._cmd_history(rest)
            elif command == "QUIT":
                self.send({"status": "ok", "operation": "quit"})
                return False
            else:
                raise AppError("BAD_REQUEST", f"Unknown command: {command}")
        except AppError as err:
            self.send_error(err.code, err.message)
        except Exception:
            # Catch-all so one bad command never crashes the server or this connection
            self.send_error("SERVER_ERROR", "Unexpected server error")
        return True
 
    # ---- command implementations ----

    def _cmd_register(self, rest: str):
        username = rest.strip()
        if not username:
            # <username> and later <channel> meant to tell user how to correctly use command
            raise AppError("BAD_REQUEST", "Usage: REGISTER <username>")
        user = self.state.create_user(username)
        self.send({"status": "ok", "operation": "register", "username": user.username})

    def _cmd_login(self, rest: str):
        username = rest.strip()
        if not username:
            raise AppError("BAD_REQUEST", "Usage: LOGIN <username>")
        user = self.state.require_user(username)
        self.current_user = user.username
        self.send({"status": "ok", "operation": "login", "username": user.username})

    def _cmd_logout(self):
        username = self.require_login()
        self.state.remove_user_from_all_channels(username)
        self.current_user = None
        self.send({"status": "ok", "operation": "logout", "username": username})

    def _cmd_join(self, rest: str):
        channel = rest.strip()
        if not channel:
            raise AppError("BAD_REQUEST", "Usage: JOIN <channel>")
        username = self.require_login()
        self.state.join_channel(channel, username)
        self.send({
            "status": "ok", "operation": "join",
            "channel": channel, "username": username,
        })

    def _cmd_leave(self, rest: str):
        channel = rest.strip()
        if not channel:
            raise AppError("BAD_REQUEST", "Usage: LEAVE <channel>")
        username = self.require_login()
        self.state.leave_channel(channel, username)
        self.send({
            "status": "ok", "operation": "leave",
            "channel": channel, "username": username,
        })

    def _cmd_send(self, rest: str):
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            raise AppError("BAD_REQUEST", "Usage: SEND <channel> <text>")
        channel, text = parts[0], parts[1]
        username = self.require_login()
        msg = self.state.send_message(channel, username, text)
        self.send({"status": "ok", "operation": "send", "message_id": msg.message_id})

    def _cmd_list(self, rest: str):
        sub = rest.strip()
        if sub.upper() == "CHANNELS":
            channels = self.state.list_channels()
            self.send({"status": "ok", "operation": "list_channels", "channels": channels})
            return
        if sub.upper().startswith("USERS"):
            channel = sub[len("USERS"):].strip()
            if not channel:
                raise AppError("BAD_REQUEST", "Usage: LIST USERS <channel>")
            users = self.state.list_channel_members(channel)
            self.send({
                "status": "ok", "operation": "list_users",
                "channel": channel, "users": users,
            })
            return
        raise AppError("BAD_REQUEST", "Usage: LIST CHANNELS | LIST USERS <channel>")

    def _cmd_history(self, rest: str):
        parts = rest.split()
        if not parts:
            raise AppError("BAD_REQUEST", "Usage: HISTORY <channel> [limit]")
        channel = parts[0]
        limit = None
        if len(parts) > 1:
            if not parts[1].isdigit():
                raise AppError("BAD_REQUEST", "limit must be a positive integer")
            limit = int(parts[1])
        msgs = self.state.get_history(channel, limit)
        self.send({
            "status": "ok",
            "operation": "history",
            "channel": channel,
            "messages": [m.to_dict() for m in msgs],
        })


def run_tcp_server(host: str, port: int, state: SharedState):
    """
    Listens for TCP connections and spawns one daemon thread per client,
    each running its own TCPClientHandler against the same SharedState
    the HTTP server uses making data created on one
    interface visible on another.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen()
    print(f"TCP server listening on {host}:{port}")

    while True:
        conn, addr = server_sock.accept()
        handler = TCPClientHandler(conn, addr, state)
        # daemon=True so a hung client thread never blocks process exit
        threading.Thread(target=handler.run, daemon=True).start()


def parse_args() -> argparse.Namespace:
    # TCP connection on port 9000, HTTP connection on port 8080
    parser = argparse.ArgumentParser(description="Mini Discord")
    parser.add_argument("--host", required=True, help="Host/IP to listen on, e.g. 127.0.0.1")
    parser.add_argument("--tcp-port", required=True, type=int, help="TCP port, e.g. 9000")
    parser.add_argument("--http-port", required=True, type=int, help="HTTP port, e.g. 8080")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    shared_state = SharedState()
    """
    The TCP listener runs on its own daemon thread because
    flask_app.run() below blocks the thread it's called on; both
    have to be listening at the same time against the same state.
    """
    tcp_thread = threading.Thread(
        target=run_tcp_server,
        args=(args.host, args.tcp_port, shared_state),
        daemon=True,
    )
    tcp_thread.start()

    flask_app = create_app(shared_state)
    """
    Need use_reloader=False because Flask's auto-reloader spawns a second process
    and re-execs on file changes, which would start a second separate
    TCP listener/thread and SharedState.
    Need debug=False for the same reason it's off in production: no interactive
    debugger, no code reloading, just a plain long-running server.
    """
    flask_app.run(host=args.host, port=args.http_port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()