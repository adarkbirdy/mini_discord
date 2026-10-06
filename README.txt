Mini Discord - PA1
====================================

John Kim

Files:

README.txt
run.sh
requirements.txt
server.py
client.py


DEMONSTRATION CHECKLIST
====================================

Requirement                    | Video Timestamp
-------------------------------|----------------
Server startup                 | 0:30
Health endpoint                | 0:40
User creation                  | 0:50 (for alice), 1:00 (for bob)
Channel membership             | 1:09 (alice joins) and 1:19 (bob joins), 1:32 (list)
Message creation               | 1:46
Message history                | 1:55
TCP interface                  | 2:30 - 3:20
Shared TCP and HTTP state      | 3:00 (TCP sees HTTP messages), 4:08 (HTTP sees TCP messages)
Concurrent clients             | 3:30 - 4:34
Failure detection              | 4:40 - 5:12 (list without charlie at 4:54, ok health check at 5:07)


OVERVIEW
====================================

The server keeps all state (users, channels, memberships, messages) in a
single in-memory SharedState object (server.py). Both the HTTP interface
and the TCP interface hold a reference to the exact same SharedState
instance, so data created through one interface is immediately visible
through the other.

SharedState is protected by one threading.RLock. Every method that reads
or writes shared dictionaries/sets/lists acquires this lock, so concurrent
requests from multiple HTTP threads and multiple TCP client threads cannot
race on the same data. RLock (reentrant lock) is used instead of a plain
Lock because some SharedState methods call other SharedState methods
internally (e.g. join_channel calls require_user) while still holding the
lock; a plain Lock would deadlock itself in that case.

Validation errors (unknown user, duplicate user, unknown channel, not
authenticated, etc.) are raised from SharedState as a single AppError
exception type carrying one of the expected error codes.
Both the HTTP layer and the TCP layer catch AppError in one place and
translate it into their own response format. This means the same
validation logic backs both interfaces, and neither interface had to
duplicate it.

HTTP interface (Flask):
  create_app(state) builds a Flask app wired to a given SharedState.
  Routes cover /health, /users, /channels, /channels/<c>/members,
  /channels/<c>/users, /channels/<c>/messages, matching the endpoints,
  status codes, and JSON shapes required by the specs. A Flask
  errorhandler for AppError, 404, and 405 guarantees every response
  (success or failure) is JSON with the required Content-Type.

TCP interface (TCP sockets):
  run_tcp_server(host, port, state) listens for TCP connections and spawns
  one daemon thread per client (TCPClientHandler). Each handler reads
  one line at a time, parses the first word as a command, dispatches to
  a handler method, and writes back exactly one JSON response line per
  command. A per-connection current_user attribute tracks login state
  for that connection only.

Startup:
  server.py's main() parses --host, --tcp-port, --http-port with
  argparse, starts the TCP listener on a background daemon thread, then
  runs the Flask app on the main thread. run.sh forwards its arguments
  straight into server.py after making sure Flask is installed.


DEPENDENCIES
====================================

- Python 3.10+ (uses dict[str, X] / list[X] built-in generics and match-style
  type hints)
- Flask (listed in requirements.txt)

No database, message broker, or external services are used.

STARTUP INSTRUCTIONS
====================================

From this directory:

    ./run.sh --host 127.0.0.1 --tcp-port 9000 --http-port 8080

run.sh will install Flask automatically (via pip) the first time, if it
isn't already installed, then start the server. No interactive input is
required after startup.

To install dependencies manually instead:

    pip install -r requirements.txt
    py server.py --host 127.0.0.1 --tcp-port 9000 --http-port 8080

To run the TCP client against a running server:

    py client.py --host 127.0.0.1 --port 9000


NOTE: It may be necessary to use "python" or "python3" instead of "py"
depending on your environment. This goes for any commands later that use "py" too.

TESTING INSTRUCTIONS
====================================

NOTE: Everything is done inside Git Bash shells.

1. Start the server as shown above.
2. Health check in another terminal:
     curl http://127.0.0.1:8080/health
3. Create users and exercise the HTTP endpoints with curl, following the
   examples in the assignment description (create users, join a channel,
   send/retrieve messages).
4. Run the TCP client (py client.py --host 127.0.0.1 --port 9000)
   in a separate terminal and try REGISTER, LOGIN, JOIN, SEND, HISTORY,
   LIST CHANNELS, LIST USERS, LOGOUT, QUIT.
5. To test shared state: create/join/send via one interface, retrieve via
   the other (e.g. send a message over TCP, then GET it back over HTTP).
6. To test concurrency: open 2-3 TCP client windows at once, join the
   same channel from each, and confirm messages from all of them appear
   in a shared HISTORY / GET .../messages call.
7. To test failure detection: join a TCP client to a channel, then close
   its terminal/process without sending QUIT. Then call
     curl http://127.0.0.1:8080/channels/%23cs249/users
   and confirm that user is no longer listed, and that the server is
   still responding to new requests (for example, curl http://127.0.0.1:8080/health).
