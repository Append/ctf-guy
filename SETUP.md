# System Setup — Prerequisites

These are system-level dependencies that live **outside** the project's nix flake. Install them once per machine.

## 1. Nix (Determinate Systems)

The project uses a nix flake for all CTF tools. [Determinate Nix](https://determinate.systems/nix-installer/) is the recommended installer for first-time users — it enables flakes by default and is easier to uninstall than upstream nix.

```bash
curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix | sh -s -- install
```

After install, restart your shell or `source /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh`.

Verify:
```bash
nix --version
```

## 2. direnv

Automatically loads the nix flake environment when you `cd` into the project.

```bash
nix profile install nixpkgs#direnv nixpkgs#nix-direnv
```

Add the hook to your shell config:

**zsh** (`~/.zshrc`):
```bash
eval "$(direnv hook zsh)"
```

**bash** (`~/.bashrc`):
```bash
eval "$(direnv hook bash)"
```

Set up nix-direnv (makes `use flake` work):
```bash
mkdir -p ~/.config/direnv
cat >> ~/.config/direnv/direnvrc << 'EOF'
source $HOME/.nix-profile/share/nix-direnv/direnvrc
EOF
```

Then allow the project:
```bash
cd /path/to/ctf-guy
direnv allow
```

## 3. Docker

Required for devcontainer isolation mode (`CTF_ISOLATION=devcontainer`). Optional otherwise.

**WSL2 (recommended):** Install [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) with WSL2 backend enabled.

**Linux:**
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

Verify:
```bash
docker --version
```

## 4. Tailscale

Used for secure sharing of `/interact` terminal sessions across your team. Only people on your tailnet can access the shared terminals.

```bash
# Install
curl -fsSL https://tailscale.com/install.sh | sh

# Start the daemon
# WSL2 (no systemd):
sudo tailscaled --state=/var/lib/tailscale/tailscaled.state &
# Linux with systemd:
sudo systemctl enable --now tailscaled

# Authenticate
sudo tailscale up
```

This opens a browser link to authenticate. After connecting, your machine gets a Tailscale hostname (e.g. `your-machine.tailnet-name.ts.net`) that the bot uses for terminal URLs.

Verify:
```bash
tailscale status
```

**Note:** On WSL2, `tailscaled` needs to be started manually each boot since WSL2 doesn't persist services. Add to your `~/.bashrc` or `~/.zshrc`:
```bash
# Auto-start tailscaled on WSL2
if grep -qi microsoft /proc/version 2>/dev/null; then
  if ! pgrep -x tailscaled > /dev/null; then
    sudo tailscaled --state=/var/lib/tailscale/tailscaled.state > /dev/null 2>&1 &
  fi
fi
```

## 5. Claude Code

The AI solver. Requires a Claude Max subscription or API key.

```bash
# Install via npm
npm install -g @anthropic-ai/claude-code

# Authenticate
claude /login
```

Verify:
```bash
claude --version
```

## 6. Discord Bot Application

Create once at https://discord.com/developers/applications. See [QUICKSTART.md](QUICKSTART.md) for step-by-step.

## 7. OpenRouter Account

Used for the bot's internal AI tasks (triage, learning). Sign up at https://openrouter.ai and get an API key.

---

## Putting It Together

After installing all prerequisites:

```bash
# Clone the repo
git clone https://github.com/Append/ctf-guy.git
cd ctf-guy

# direnv loads the nix flake automatically
direnv allow
# Wait for nix to build the environment (first time takes a few minutes)

# Configure the bot
cd bot
cp .env.example .env
nano .env  # Fill in tokens

# Install Python deps
uv sync

# Run
uv run python run.py
```

### Telemetry (optional)

The Grafana/VictoriaMetrics stack reads a **second** env file at the repo root.
This is separate from `bot/.env` above — compose only reads the root one.

```bash
# From the repo root, not bot/
cp .env.example .env
nano .env
```

`GRAFANA_ADMIN_PASSWORD` is required and has no default: the stack refuses to
start without it. An empty value counts as unset, so it needs a real password.

```bash
docker compose -f docker-compose.telemetry.yml up -d
```

Grafana comes up on `localhost:3000` (`admin` / whatever you set). Anonymous
access is off, so it will prompt for login.

> Grafana only applies `GRAFANA_ADMIN_PASSWORD` when it first initializes its
> database. If the `grafana-data` volume already exists, the stored password
> wins and changes to `.env` are ignored. Reset it with
> `docker compose -f docker-compose.telemetry.yml exec grafana grafana-cli admin reset-admin-password '<new>'`,
> or wipe the volume with `down -v` to re-provision from scratch.

## Platform-specific Notes

### WSL2
- GPU (hashcat): Works if you have an NVIDIA GPU. The flake auto-exports `LD_LIBRARY_PATH` for CUDA.
- Docker: Use Docker Desktop with WSL2 backend.
- Tailscale: Daemon must be started manually each boot (see section 4).
- Playwright: Needs a display server for headed mode. Install `xdg-utils` or run with `DISPLAY=:0` if you have an X server.

### macOS
- Docker: Use Docker Desktop for Mac.
- GPU: hashcat GPU mode not available (no CUDA).
- Everything else works the same.

### Linux (native)
- Everything works out of the box.
- Use systemd for tailscaled and docker.
