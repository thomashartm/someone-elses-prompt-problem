/**
 * SEPP frontend — Somebody Else's Problem.
 *
 * Registers the "SEPP: send to Hunt" context-menu entries for request rows
 * and single requests (HTTP History, Intercept, request views), collects the
 * selected request IDs and hands them to the backend, which declares them
 * Somebody Else's Problem (the Hunt agent's). Deliberately UI-free: no Vue,
 * no page, no styles — commands, menu items and toasts only.
 */
import type { Caido } from "@caido/sdk-frontend";
import type { SendResult } from "../shared";

/** Backend endpoints exposed by src/backend/index.ts via sdk.api.register.
 * Typed here so sdk.backend.declareSep is fully checked on the frontend. */
type SepEndpoints = {
  declareSep: (ids: string[]) => Promise<SendResult[]>;
};

type CaidoSDK = Caido<SepEndpoints, Record<string, never>>;

function extractRequestIds(context: Record<string, unknown>): string[] {
  const ids: string[] = [];
  if ("requests" in context && Array.isArray(context.requests)) {
    for (const r of context.requests) {
      if ((r as Record<string, unknown>).id) ids.push(String((r as Record<string, unknown>).id));
    }
  }
  if ("request" in context && context.request) {
    const req = context.request as Record<string, unknown>;
    if (req.id) ids.push(String(req.id));
  }
  return ids;
}

export const init = (sdk: CaidoSDK) => {
  sdk.commands.register("sepp", {
    name: "SEPP: send to Hunt",
    group: "SEPP",
    run: async (context: unknown) => {
      const ids = extractRequestIds(context as Record<string, unknown>);
      if (ids.length === 0) {
        sdk.window.showToast("SEPP: no request selected", { variant: "warning", duration: 3000 });
        return;
      }
      sdk.window.showToast(`Declaring SEPP for ${ids.length} request(s)...`, { variant: "info", duration: 2000 });
      const results = await sdk.backend.declareSep(ids);
      const failed = results.filter((r: SendResult) => !r.ok);
      if (failed.length === 0) {
        const last = results[results.length - 1]?.url ?? "";
        sdk.window.showToast(
          results.length === 1 ? `SEPP declared: ${last} — Hunt has it` : `${results.length} requests declared SEPP — Hunt has them`,
          { variant: "success", duration: 4000 },
        );
      } else {
        sdk.window.showToast(
          `SEPP: ${failed.length}/${results.length} failed — ${failed[0]?.info ?? "?"}`,
          { variant: "warning", duration: 6000 },
        );
      }
    },
  });

  sdk.menu.registerItem({
    type: "RequestRow",
    commandId: "sepp",
    leadingIcon: "fas fa-person-walking-arrow-right",
  });
  sdk.menu.registerItem({
    type: "Request",
    commandId: "sepp",
    leadingIcon: "fas fa-person-walking-arrow-right",
  });
  sdk.commandPalette.register("sepp");
};
