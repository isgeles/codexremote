# Codex Remote reference

Use Codex on your machine from a browser on your laptop or phone. A lightweight, self-hosted web app with mobile-friendly chats, files, images, approvals, and slash commands.

**Python 3.10+ · Linux / macOS · MIT licensed**

The WebSocket dependency is installed automatically; the legacy `[shared]` extra remains accepted for compatibility.

## Install

First install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) on the machine where your projects live and sign in with `codex login`.

Install Codex Remote directly from GitHub:

```bash
pip install 'git+https://github.com/isgeles/codexremote.git'
```

Or install it in an isolated tool environment with [uv](https://docs.astral.sh/uv/guides/tools/):

```bash
uv tool install 'git+https://github.com/isgeles/codexremote.git'
```

`pipx install 'git+https://github.com/isgeles/codexremote.git'` also works. If your system Python prevents direct package installation, use a virtual environment, pipx, or uv. Git is required for these GitHub installation commands. This project is pip-installable from GitHub; **it has not been published to PyPI**.

From a source checkout:

```bash
git clone https://github.com/isgeles/codexremote.git
cd codexremote
pip install .
```

## Run

In a project folder on the machine:

```bash
codexremote
```

The server starts in the background and prints your machine's browser URL and access token:

```text
Codex Remote is running
  Open:  http://192.168.1.20:8787
  Open:  http://127.0.0.1:8787
  Token: <your-generated-access-token>
  Folders: /home/you/projects/my-project
```

Open the printed network URL from your laptop or phone and paste the token. No shell alias is required: installation creates the `codexremote` executable. Closing your terminal or disconnecting SSH leaves the background server running.

```bash
codexremote stop       # Stop the web client; terminal sessions keep running
codexremote restart    # Restart with the previous folders and settings
codexremote status     # Show running status, browser URLs, token, and log paths
codexremote -h         # Brief usage and options
codexremote help      # Same help
codexremote --version
```

These commands work from any folder. Running `codexremote` again while it is running prints the existing connection details. `stop` is safe to repeat. `restart` retains settings and the token; explicit flags override the saved settings. A fresh `start` after stopping uses the current directory and default options unless flags are supplied. One server is managed per state directory.

Use the browser's **Add to Home Screen** action for a standalone phone window where supported.

## Continue your terminal chat from your phone

Run Codex normally in your terminal, and start the phone interface on the same machine:

```bash
codexremote start
```

Open the printed URL on your phone, sign in with the printed token, and select your terminal conversation. Leave the terminal open: both clients use the same live session, so you can continue from either device without stopping or transferring it. No `--connect` flag, shared extra, or special terminal command is needed.

By default, Codex Remote attaches to Codex's local per-user app-server daemon, starting it only if necessary. It connects to the daemon’s local Unix WebSocket socket. **Stopping or restarting Codex Remote disconnects only the web client**, leaving the daemon and terminal sessions running. Closing the phone browser also leaves work running.

This requires a Codex CLI with `app-server daemon start` support and a managed standalone installation (verified with 0.154.0). Use the same OS user and `CODEX_HOME` as your terminal. A terminal explicitly connected to a different server, or an older standalone Codex process, is outside that local daemon; its active session cannot be transferred automatically. Update older Codex installations for the default shared workflow.

An already-running older Codex Remote instance keeps its existing transport until restarted. Finish any work owned by that older web instance before restarting it to apply the new default. Terminal sessions are not restarted.

### Explicit external servers

For an advanced setup with a different WebSocket server, use `codexremote --connect ws://127.0.0.1:4500`. The terminal must use that same server. Restart remembers this explicit endpoint; `codexremote restart --connect '' --connect-token-file ''` returns to the default local daemon.

WebSocket connections accept `ws://` on loopback and `wss://` for remote endpoints. Use `--connect-token-file /path/to/backend-token` for backend authentication, distinct from the browser access token. The web client and backend need identical filesystem paths for files and attachments.

If the backend connection drops, make sure it is reachable and restart the web client. Pending approval replay after attachment depends on the installed Codex server; use the original client for requests that predate attachment and are not replayed.

## Options

```bash
# Share multiple project folders (first one is the default working folder).
codexremote --root ~/projects --root ~/work

# Apply different folders or a port to the running server.
codexremote restart --root ~/projects --port 9000

# Loopback only, for SSH forwarding or an HTTPS reverse proxy.
codexremote restart --host 127.0.0.1

# Keep the server in the foreground for a process supervisor or debugging.
codexremote --foreground

# Use a particular Codex installation.
codexremote --codex /path/to/codex
```

The default listener is **0.0.0.0:8787**, so the app is reachable through your machine's IPv4 network interfaces. The printed IP is detected at startup; VPNs and multiple interfaces can affect which address is shown. If needed, use the machine IP you already SSH to. Your firewall must permit the port from your network. For IPv6, use an explicit `--host` address or `--host ::`.

`--root` limits the web file browser and sets the initial project. New conversations can choose subfolders using the folder button. Codex itself follows its configured permissions and can resume saved conversations with other working directories.

## Network access

Direct HTTP is intended for a trusted private network. The access token grants control of your local Codex account; use an SSH tunnel, encrypted private VPN, or HTTPS when crossing an untrusted network. No router port-forwarding or firewall changes are performed automatically.

For SSH access, run the server with `--host 127.0.0.1`, then on your laptop:

```bash
ssh -N -L 8787:127.0.0.1:8787 you@your-machine
```

Open `http://127.0.0.1:8787` on the laptop. A phone SSH client can configure the same local forward.

For public HTTPS access, keep the app on loopback and put a TLS reverse proxy in front:

```bash
codexremote restart --host 127.0.0.1 --public-url https://codex.example.com
```

See [the Caddy example](../deploy/Caddyfile.example). The proxy must preserve Host. The app checks the exact `--public-url` origin; an empty value (`--public-url ''`) clears a previous setting.

Direct TLS is also supported:

```bash
codexremote --host 0.0.0.0 --public-url https://machine.example:8787 \
  --tls-cert /path/to/cert.pem --tls-key /path/to/key.pem
```

HTTPS is needed for secure-context browser features such as clipboard access outside localhost. Notifications are best effort while a supported browser context remains alive; there is no Web Push service or offline agent operation.

## State, tokens, and logs

Runtime data is kept outside the installed package:

- Linux: `$XDG_STATE_HOME/codexremote`, or `~/.local/state/codexremote`.
- macOS: `~/Library/Application Support/codexremote`.
- Override with `CODEXREMOTE_STATE_DIR` or `--state-dir /path/to/state`.

Use the same override for start/stop/restart/status if managing an additional instance. It also needs a different web port. `codexremote status` prints the exact log locations and token.

The directory is private to your user. `access-token` persists across restarts; browser cookies expire on restart, so sign in again using that same token. `config.json` holds the last start settings; `runtime.json` identifies the currently managed instance. `server.log` contains startup errors, `codex.log` contains app-server diagnostics, and `uploads/` holds attachments. Installation upgrades preserve these files and your existing Codex history.

Process control uses a separate authenticated loopback connection with a per-instance secret. It never kills an arbitrary process based on a stale PID file or a occupied web port. Locking prevents two simultaneous starts for the same state directory. If startup fails, read the printed logs; install/sign into Codex if needed, or choose another port.

To rotate the access token, stop the app, replace `access-token` with a random value of at least 32 characters, keep file permissions at `600`, then start again. Runtime secrets are not bundled in wheels, source distributions, or the repository.

For startup at login/boot, the foreground mode works with process supervisors. An optional [Linux user-service example](../deploy/codex-remote.service.example) is included. The CLI does not install a system service automatically.

## What works

- Browse/search/page through local Codex conversations, resume them, rename, archive/restore, fork, compact, and export.
- Stream replies, plans, terminal output, tool calls, file changes, reasoning summaries, and generated images when Codex supplies them.
- Start and interrupt turns; send follow-ups to steer a running turn.
- Set and inspect long-running thread goals with an optional token budget.
- Model and reasoning selection from the installed Codex catalog, Code/Plan modes, and permission choices.
- Command/file approvals, session approvals, permission requests, interactive questions, and basic MCP elicitation forms.
- File and image upload, paste images, drag/drop, camera/gallery selection when provided by the phone's file picker.
- View text and raster image files, download files, and attach existing files or skills.
- Inspect account/usage, installed skills, MCP servers, and recent protocol events.
- Advanced JSON protocol console for installed app-server methods that lack dedicated UI controls.
- Work continues when the phone sleeps or loses network; reconnect reloads saved history and pending approvals. Multiple browser tabs can observe and answer the same approvals.

## Markdown and math

Chats support headings, emphasis, nested lists, task lists, blockquotes, tables, links, strikethrough, and fenced code blocks with copy buttons. Local file links open the file viewer.

Write inline math as `$E = mc^2$` or `\(E = mc^2\)`, and display math with `$$ ... $$` or `\[ ... \]`. Multiline display equations, matrices, fractions, and aligned equations use KaTeX. Code blocks and inline code stay literal. Escape a dollar sign as `\$` when writing currency. Incomplete or unsupported LaTeX remains readable as source. Wide equations scroll inside the message on phones.

[markdown-it](https://github.com/markdown-it/markdown-it) and [KaTeX](https://katex.org/docs/options) are bundled, including math fonts; rendering requires no CDN or Node installation. See [third-party notices](../THIRD_PARTY_NOTICES.md).

## Slash commands

Type `/` in the message box to open the command menu. Filter by typing, use arrow keys and Enter, or tap a command on your phone. Tab completes the selected command; Escape closes the menu. `/help` shows the complete supported list.

| Command | Web app behavior |
| --- | --- |
| `/new`, `/clear` | Start a fresh conversation in the current folder |
| `/resume` | Open the conversation list and search |
| `/model [model]` | Open model settings, or select a catalog model by ID |
| `/reasoning [effort]` | Open reasoning settings, or select a supported effort (`default` resets it) |
| `/permissions [default\|read-only\|workspace-write]` | Open permissions or select the next turn's policy; `/approvals` is an alias |
| `/plan [prompt]` | Enter Plan mode; optionally send the supplied planning request |
| `/code` | Return to Code mode for the next message |
| `/review` | Review uncommitted changes in the current conversation |
| `/compact` | Compact the current conversation's context |
| `/fork` | Branch the current conversation |
| `/rename [name]` | Open the naming dialog, or rename directly |
| `/diff` | Show the latest captured Codex turn diff (not a full Git working-tree diff) |
| `/goal` | Open the long-running goal controls |
| `/status` | Show account, machine, and usage information |
| `/skills`, `/mcp` | Browse skills or inspect MCP servers |
| `/files [folder]` | Browse shared files |

Commands are handled by the web client, using its existing controls and app-server operations. Unknown commands produce a message instead of being sent to the model. Use `//` to send text that starts with a literal slash: `//model` sends `/model` as ordinary text. Multi-component file paths such as `/home/user/project` are also sent as ordinary text. `/plan <prompt>` is the command that sends an inline prompt; it preserves any attachments.

The command set follows the [official Codex command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli) where implemented. `/code`, `/files`, and `/help` are web-client shortcuts. Terminal-only commands, custom `/prompts:` commands, and commands not shown in `/help` are not implemented here. Use the Stop button to interrupt an active turn; `/stop` is not mapped to that button because the CLI command has different semantics.

Uploads are stored under `uploads/` in the state directory with unique directories and never overwrite project files. Non-image attachments are passed to Codex as local file paths for its tools to read. Images use the protocol's `localImage` input. Upload limit: 25 MB per file. Text previews: first 1 MB. Browser downloads: up to 250 MB. Uploads remain on disk until you remove them; do not remove files still referenced by conversations.

## Scope and limitations

This is an independent client for the [official Codex app-server protocol](https://learn.chatgpt.com/docs/app-server), tested with Codex CLI 0.154.0. It is not a complete clone of ChatGPT.

- It uses your existing Codex login and local configuration. No separate API key is required when Codex is signed in with ChatGPT. Your model/account limits still apply.
- It shows local Codex conversations, not ordinary ChatGPT cloud chats. By default it shares the local terminal daemon. With `--connect`, it joins an explicitly selected app-server, allowing multiple clients to use that server’s live threads. Independent app-server processes cannot write to the same thread simultaneously.
- Available tools, skills, and integrations come from your Codex setup. Cloud-only ChatGPT features, voice/realtime calls, first-party plugin interfaces, and desktop computer-use interfaces are not recreated here.
- Complex integration setup can use the advanced protocol console or CLI configuration. Unknown server request types have a manual JSON-response UI.
- Stopping/restarting only disconnects the web client; it does not stop the shared Codex daemon or its active work. Persisted conversations remain resumable. A browser disconnect alone does not stop agent work.
- This is a single-user personal tool, without teams, multi-user isolation, background Web Push, or Windows-native process management (use WSL on Windows).
- The protocol evolves with Codex. Retest when upgrading the CLI.

## Security model

The authenticated owner has the installed Codex account's full protocol capabilities, including command execution and configuration. Shared web folders are **not a sandbox around Codex**; its configured permissions and approval policy govern agent access.

The app checks Host/Origin headers, rejects cross-site requests, uses HttpOnly SameSite session cookies, limits failed logins, and applies a restrictive content security policy. Raw Markdown HTML is disabled; math renders with untrusted commands blocked. Workspace HTML/SVG is downloaded instead of executed. The file browser resolves paths, rejects traversal and symlinks outside shared roots, and excludes private runtime/configuration directories. Avoid sharing folders containing files you don't want to access remotely.

## Development and verification

```bash
pip install -e .
python -m unittest discover -s tests -p 'test_*.py' -v
```

Tests use a deterministic fake Codex. They cover authentication, origin checks, file boundaries, uploads/downloads, concurrent RPCs, pending approvals, replay gaps, background/foreground lifecycle, restart settings, concurrent starts, stale PIDs, and control authentication.

Verify default daemon attachment and session survival across web reconnections against a real managed Codex installation, with temporary state and no copied login credentials:

```bash
python tests/local_daemon_smoke.py
```

Optional browser tests (test dependencies only):

```bash
uv run --python 3.12 --with playwright python -m playwright install chromium
uv run --python 3.12 --with playwright python tests/browser_check.py
uv run --python 3.12 --with playwright python tests/slash_check.py
uv run --python 3.12 --with playwright python tests/markdown_check.py
```

Browser tests exercise desktop/phone chats, reconnects, approvals, attachments, image previews, slash commands, and injection handling. Screenshots go to `test-results/`.

An opt-in real integration test creates one read-only conversation, requests a short model response, checks persisted history, and archives the test conversation. It consumes model usage:

```bash
python tests/live_smoke.py
# Separate shared server + temporary Codex state; consumes two short model turns:
uv run --python 3.12 --no-project --with websockets python tests/shared_session_smoke.py
```

Build an installable wheel and source distribution:

```bash
pip install build
python -m build
# Or: uv build
python tests/wheel_check.py
```

`codexremote/cli.py` manages the CLI and lifecycle. `codexremote/server.py` supplies HTTP/auth/files and the bidirectional Codex JSONL bridge. `codexremote/web/` contains the bundled frontend. The top-level `server.py` and `web` link preserve source-checkout compatibility.

## License

[MIT](../LICENSE) © 2026 Ismail Geles.
