# Codex Remote

Continue your computer’s Codex chat from your phone. Keep the terminal open, read live replies, send messages, and approve requests from either device.

**This project is vibe coded. Expect rough edges.** For first-time setup, close all existing Codex sessions, start Codex Remote, then reopen Codex. This puts your terminal and phone on the same shared server and avoids “active writer” errors.

## Why Codex Remote?

Use this when you want a self-hosted browser interface to the Codex sessions already running on your computer:

- Continue the same chat from your terminal and phone, with access to your local projects and tools.
- Connect directly over trusted Wi-Fi or a private VPN, without installing a separate phone app.
- Sign into the web interface with its generated access token. Once the computer's Codex CLI is signed in, this interface adds no separate account login or 2FA step on your phone.

The access token is still required on a private network. This does not bypass any authentication required by Codex or your VPN.

## Quick start

Requires Linux or macOS, Python 3.10+, and a current managed standalone [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), signed in with `codex login`. Shared sessions were tested with Codex 0.154.0.

1. **Install Codex Remote** on the computer where you run Codex:

   ```bash
   uv tool install 'git+https://github.com/isgeles/codexremote.git'
   ```

   You can also use `pip install` with the same URL.

2. **Let any running work finish, then close all Codex sessions.** This is a one-time setup step; you do not need to close them when switching devices afterward.

3. **Start Codex Remote** from your project folder:

   ```bash
   codexremote start
   ```

   It prints a browser URL and access token. If an older Codex Remote instance is already running, use `codexremote restart` to apply the update.

4. **Reopen Codex in your terminal**, and start or resume your chat:

   ```bash
   codex
   # Or resume an existing chat:
   codex resume
   ```

5. **Open the printed network URL on your phone**, enter the token, and select the same chat. Keep both devices open and continue from either one. No sharing flags are needed.

Use the same computer user and Codex configuration (`CODEX_HOME`, if set) for both commands. Your phone must be able to reach the computer; start on the same trusted Wi-Fi or private VPN. Keep the computer awake.

## Everyday use

```bash
codexremote status     # Show the URL, token, and log paths
codexremote restart    # Restart the web interface
codexremote stop       # Stop the web interface
codexremote --help     # See all options
```

Stopping the web interface or closing your phone browser leaves shared Codex sessions running. Add the site to your phone’s home screen for quicker access.

Approvals appear above the message box, with buttons kept visible while you scroll the details. You can also attach files and images, browse project files, and type `/` for commands.

## Troubleshooting

- **“Active writer” when opening a chat:** an existing Codex process owns it outside the shared server. Finish its work, close that Codex session, start Codex Remote, then resume the chat in Codex. Do not delete writer locks.
- **Phone cannot connect:** use the printed network address rather than `127.0.0.1`. Check that both devices are on the same reachable network and the computer allows port 8787.
- **Startup fails:** run `codexremote status` for log paths. Confirm Codex is installed and signed in.

Keep the access token private: it grants access to your Codex account and machine through the app. Use a trusted private network, VPN, SSH tunnel, or HTTPS; do not expose plain HTTP directly to the internet.

See the [reference guide](docs/reference.md) for advanced settings, SSH/HTTPS, external servers, supported features, and development checks.

[MIT license](LICENSE) · Independent project, not an official OpenAI app.
