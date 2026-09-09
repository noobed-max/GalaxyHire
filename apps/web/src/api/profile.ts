import type { ApiFetch } from "./types";

export const profileApi = {
  get: (api: ApiFetch) => api("/api/v1/profile"),
  save: (api: ApiFetch, body: unknown) => api("/api/v1/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
};
