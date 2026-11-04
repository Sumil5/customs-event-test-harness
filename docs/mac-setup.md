# Running it on an Apple Silicon Mac

This guide assumes a Mac Studio with an M4 Max and 36 GB of RAM, but any Apple Silicon Mac with 16 GB or more will do.

## What it needs

| Resource | Needed | Notes |
|---|---|---|
| Memory for Docker | 6 GB minimum, 8 GB recommended | Oracle takes about 2–2.5 GB, Kafka about 1 GB, and the services and moto take a few hundred MB |
| Disk | about 10 GB | Mostly the Oracle image. Docker images live inside the runtime's VM disk |
| CPU | any | The pipeline is I/O-bound |
| Emulation | none | Kafka, Oracle Free, moto, Python and kafka-ui all publish native **arm64** images, so Rosetta is not needed |

## 1. Command-line tools

```bash
xcode-select --install
```

This installs git and `make`. Skip it if `git --version` already works.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

This installs Homebrew. Skip it if you already have it.

```bash
brew install uv
```

`uv` manages Python too. It downloads Python 3.13 for this project automatically, so you don't need a separate Python install.

## 2. A Docker runtime

Pick one:

- **OrbStack** (recommended on Apple Silicon): lighter, quicker to start and gentler on the battery. Free for personal use.

  ```bash
  brew install --cask orbstack
  ```

  It manages memory dynamically, so there's nothing to tune.

- **Docker Desktop**: free for personal use and small businesses.

  ```bash
  brew install --cask docker
  ```

  Then go to **Settings → Resources** and give it at least **8 GB** of memory.

Check it works:

```bash
docker run --rm hello-world
```

## 3. Get the project running

```bash
git clone https://gitlab.com/sd00154/customs-event-test-harness.git
```

```bash
cd customs-event-test-harness
```

```bash
make install
```

```bash
make up
```

The first `make up` downloads about 1.7 GB of images, so allow a few minutes. After that the stack is healthy in about 30 seconds.

```bash
make test
```

That runs the unit tests, which need no Docker.

```bash
make e2e
```

That runs the end-to-end tests against the stack.

```bash
make demo
```

That publishes 2,000 messages, then reconciles and traces them.

Open the reports:

```bash
open "reports/$(cat reports/LATEST)/reconciliation.html"
```

Optionally, browse the topics in a Kafka UI at http://localhost:8080:

```bash
make ui
```

## Ports used on your Mac

| Port | What |
|---|---|
| 9092 | Kafka (for the harness running on your Mac) |
| 1521 | Oracle (`customs/customs_pw@localhost:1521/FREEPDB1`) |
| 5050 | moto, the CloudWatch Logs emulator. Not port 5000, because macOS's **AirPlay Receiver** already uses it |
| 8080 | Kafka UI (only after `make ui`) |

If a port is already taken, change the left-hand number in `docker-compose.yml`. For Kafka you must also change the `EXTERNAL://localhost:9092` advertised listener.

## Connecting a SQL client

SQL Developer, DBeaver and DataGrip all work. Use these settings:

- host `localhost`
- port `1521`
- service name `FREEPDB1`
- user `customs`
- password `customs_pw`

Useful queries:

```sql
SELECT lane, COUNT(*) FROM declaration GROUP BY lane;
SELECT * FROM declaration ORDER BY persisted_at DESC FETCH FIRST 20 ROWS ONLY;
```

## Day-to-day

| Task | Command |
|---|---|
| Stop everything and wipe the data | `make down` |
| Follow the services' JSON logs | `make logs` |
| Run against faulty services | `make demo-faults` |
| Format and lint | `make format`, `make lint` |
| Use the CLI directly | `uv run harness --help` |

For the IDE, open the folder in VS Code or PyCharm and select `.venv/bin/python` as the interpreter.

## Troubleshooting

- **`oracle` never becomes healthy.** Run `docker compose logs oracle`. This is almost always memory: give Docker at least 6 GB.
- **`port is already allocated`.** Something else is using that port. See the ports table above.
- **The tests skip with "Kafka and Oracle not reachable".** Start the stack with `make up`.
- **CloudWatch test fails after restarting only moto.** moto keeps everything in memory, so restarting it deletes the log groups. Run `make up` or restart the services: `docker compose up -d --force-recreate intake assessment persistence`.
- **You want a completely clean slate.** Run `make down && make up`. Oracle data is not persisted between runs, by design.
