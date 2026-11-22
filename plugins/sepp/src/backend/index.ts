/**
 * sepp backend.
 *
 * For each selected Caido request: extract fields, write the RAW request
 * bytes to a temp file, and invoke scripts/sepp.sh (the same receiver
 * dispatch would call). The script copies the temp file synchronously before
 * exiting, so cleanup after the child closes is safe.
 *
 * Process-spawn pattern and request-field extraction are adapted from
 * six2dez/dispatch (MIT) - see NOTICE.
 */
import { spawn } from "child_process";
import { mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { SDK, DefineAPI } from "caido:plugin";
import type { SendResult, SendToPiEvents } from "../shared";

/** Resolve `sepp` through the user's LOGIN SHELL so the installation
 * location is irrelevant (global symlink via `make install-cli`). Mirrors
 * dispatch's executor convention (/bin/zsh -lc on macOS). Args travel as
 * positional parameters - no shell-quoting hazards. */
const LOGIN_SHELL = process.env.SHELL || "/bin/zsh";
const TOOL = "sepp";

interface ReqFields {
  url: string;
  method: string;
  host: string;
  port: string;
  scheme: string;
  path: string;
  query: string;
  cookies: string;
}

function fieldsOf(item: {
  request: {
    getHost(): string;
    getPort(): number;
    getPath(): string;
    getQuery(): string | undefined;
    getMethod(): string;
    getTls(): boolean;
    getHeader(name: string): string[] | string | undefined;
    getRaw(): { toBytes(): Uint8Array };
  };
}): ReqFields {
  const request = item.request;
  const host = request.getHost();
  const port = request.getPort();
  const path = request.getPath();
  const query = request.getQuery() ?? "";
  const method = request.getMethod();
  const scheme = request.getTls() ? "https" : "http";
  const cookieValues = request.getHeader("Cookie");
  const cookies = Array.isArray(cookieValues) ? cookieValues.join("; ") : "";

  const standardPort = (scheme === "https" && port === 443) || (scheme === "http" && port === 80);
  const url = `${scheme}://${host}${standardPort ? "" : `:${port}`}${path}${query ? `?${query}` : ""}`;

  return { url, method, host, port: String(port), scheme, path, query, cookies };
}

type MinimalSDK = SDK<DefineAPI<{ declareSep: typeof declareSep }>, SendToPiEvents>;

async function sendOne(sdk: MinimalSDK, requestId: string): Promise<SendResult> {
  try {
    const item = await sdk.requests.get(requestId);
    if (!item) {
      return { requestId, url: "", ok: false, info: `request ${requestId} not found` };
    }
    const f = fieldsOf(item);

    // Exact request bytes (binary-safe), like dispatch's %R.
    const tmpDir = mkdtempSync(join(tmpdir(), "sepp-"));
    const rawFile = join(tmpDir, "request.http");
    writeFileSync(rawFile, item.request.getRaw().toBytes());

    return await new Promise<SendResult>((resolve) => {
      const child = spawn(
        LOGIN_SHELL,
        ["-lc", `exec ${TOOL} "$@"`, TOOL,
         f.url, f.method, rawFile, f.cookies, f.host, f.port, f.scheme, f.path, f.query],
        { stdio: ["ignore", "pipe", "pipe"] },
      );
      let out = "";
      const grab = (chunk: Buffer | string) => {
        out += chunk.toString();
      };
      child.stdout?.on("data", grab);
      child.stderr?.on("data", grab);
      child.on("error", (err) => {
        rmSync(tmpDir, { recursive: true, force: true });
        resolve({ requestId, url: f.url, ok: false, info: String(err) });
      });
      child.on("close", (code) => {
        rmSync(tmpDir, { recursive: true, force: true });
        const line = out.trim().split("\n").pop() ?? "";
        resolve({ requestId, url: f.url, ok: code === 0, info: line || `exit ${code}` });
      });
    });
  } catch (err) {
    return { requestId, url: "", ok: false, info: String(err) };
  }
}

/** Registered as the plugin's sole API function: sdk.backend.declareSep(ids). */
async function declareSep(sdk: MinimalSDK, requestIds: string[]): Promise<SendResult[]> {
  const results: SendResult[] = [];
  // Serial on purpose: one spool file per request, deterministic order.
  for (const id of requestIds) {
    results.push(await sendOne(sdk, id));
  }
  return results;
}

export type SendToPiAPI = DefineAPI<{ declareSep: typeof declareSep }>;

export function init(sdk: SDK<SendToPiAPI, SendToPiEvents>) {
  sdk.console.log("[sepp] registering backend API");
  sdk.api.register("declareSep", declareSep);
}
