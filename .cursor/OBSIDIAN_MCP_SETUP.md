<!-- [Cursor cloud edit] file created by the Cursor cloud agent -->
# Obsidian MCP setup (desktop Cursor)

This wires the **Obsidian Local REST API** plugin into **desktop** Cursor as an MCP server, using the
HTTP endpoint from the plugin's "How to access via MCP" page (`http://127.0.0.1:27123/mcp/`). The
config lives in [`.cursor/mcp.json`](./mcp.json).

> Scope: this connects your **desktop** Cursor/Claude (which runs on your machine and can reach
> `127.0.0.1`). It does **not** connect the Cursor **cloud** agent — a cloud agent runs in an
> isolated VM whose `localhost` is the VM, not your PC. To connect the cloud agent, git-mirror the
> vault to a private repo (e.g. via the Obsidian Git plugin) or tunnel the REST API over HTTPS.

## Two local steps to finish

1. **Enable the plugin + copy your API key.** In Obsidian: Settings → Community plugins → *Local REST
   API* → enable it, then copy the **API Key** shown on that settings page.
2. **Expose the key to Cursor.** `.cursor/mcp.json` reads the key from an environment variable so no
   secret is committed. Set `OBSIDIAN_API_KEY` to your key before launching Cursor, e.g.
   - macOS/Linux: add `export OBSIDIAN_API_KEY="<your-key>"` to your shell profile (`~/.zshrc` /
     `~/.bashrc`) and restart the terminal + Cursor.
   - Windows (PowerShell): `setx OBSIDIAN_API_KEY "<your-key>"`, then restart Cursor.
   - Or, if you prefer, replace `${env:OBSIDIAN_API_KEY}` in `.cursor/mcp.json` with your key
     directly (only do this in a **private** repo — never commit a key to a public one).

Then open this project in desktop Cursor → Settings → **MCP** → confirm the **obsidian** server shows
as connected (green). Your local agent can then search/read/write the vault.

## Notes

- The plugin also offers an HTTPS endpoint (`:27124`) that requires trusting its custom certificate;
  the HTTP endpoint (`:27123`) is simpler for local loopback use and is what's configured here.
- Only expose the **HTTPS** endpoint if you ever tunnel it off-machine — the HTTP endpoint sends the
  API key in clear text, which is fine on `127.0.0.1` but unsafe over a network.
