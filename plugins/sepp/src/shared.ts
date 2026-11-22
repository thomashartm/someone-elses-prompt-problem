/**
 * Shared types for the sepp Caido plugin.
 *
 * The backend registers ONE function via sdk.api.register(); the frontend
 * calls it through sdk.backend.declareSep(...). This file is the contract.
 */

export interface SendResult {
  requestId: string;
  url: string;
  ok: boolean;
  info: string;
}

export interface SendToPiAPI {
  /** Hand one or more captured requests to the Hunt agent spool. */
  declareSep(requestIds: string[]): Promise<SendResult[]>;
}

/** Events emitted to the frontend (none today). */
export type SendToPiEvents = Record<never, never>;
