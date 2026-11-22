# SEPP (Caido plugin)

**Somebody Else's Problem** — right-click any request in Caido (HTTP History,
Intercept, request views) → **SEPP: send to Hunt** → the request is declared
Somebody Else's Problem: the local SEPP's Hunt agent (pi session) inherits it as a
queued message in its live TUI.

First-party minimal plugin; context-extraction, menu-registration and
login-shell spawn patterns adapted from six2dez/dispatch (MIT) — see `NOTICE`.

## Layout

```
manifest.json          Caido plugin manifest (frontend + backend halves)
icon.svg               toolbar/menu icon
src/shared.ts          frontend<->backend contract (SendResult, declareSep)
src/backend/index.ts   extracts request fields + raw bytes, spawns `sepp` via login shell
src/frontend/index.ts  command + context-menu registration + toasts
vite.*.config.mts      esm lib builds -> dist/{backend,frontend}/script.js
scripts/pack.mjs       zips manifest + icon + dist -> dist/plugin_package.zip
```

## Build & install

From the repo root (Makefile targets exist):

```
make plugin-build          # npm install + typecheck + vite builds + pack
make install-cli           # global `sepp` command (symlink into ~/.local/bin)
```

Install `plugins/sepp/dist/plugin_package.zip` via Caido →
Plugins → Install from file. After a Caido restart the entries appear.

## Contract

The backend resolves `sepp` through the user's **login shell** (`$SHELL -lc`),
so no absolute repo path is baked in anywhere:

```
sepp <url> <method> <raw-tempfile> <cookies> <host> <port> <scheme> <path> <query>
```

The temp file holds the EXACT raw request bytes (headers + body, binary-safe);
`scripts/sepp.sh` copies it into `data/spool/` (repo located through the
symlink's real path) before exiting, then the watcher in
`.pi/extensions/sepp.ts` delivers it to the live Hunt session.

Env overrides: `SEPP_REPO` (repo root), `CAIDO_SPOOL_DIR` (spool location).

## Notes

- Pinned SDK/vite versions copied from dispatch's build (known-good with
  current Caido); bump deliberately.
- Toast errors surface per-request failures (request gone, script exit ≠ 0).
- Multi-select: serial dispatch, one spool file per request; the Hunt agent
  coalesces bursts into a single message.
