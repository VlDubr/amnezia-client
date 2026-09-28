import { http, HttpResponse } from "msw";
import type { Config, Me, MyServer } from "../src/api/types";
import { server } from "./msw";

export const EXPORT = {
  vpn_key: "vpn://KEY",
  native: "[Interface]\nPrivateKey = x\n",
  native_filename: "nl-1.conf",
  qr_svg: "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
};

export function config(over: Partial<Config> = {}): Config {
  return {
    id: 1,
    name: "Phone",
    user_id: 1,
    server_id: 10,
    server_name: "nl-1",
    container: "amnezia-awg2",
    protocol: "AmneziaWG",
    client_id: "pub=",
    status: "active",
    blocked_by: null,
    can_render: true,
    traffic: { rx: 2048, tx: 1024 },
    created_at: "2026-09-26T10:00:00Z",
    ...over,
  };
}

export function me(over: Partial<Me> = {}): Me {
  return {
    id: 1,
    display_name: "Ivan",
    login: "ivan",
    status: "active",
    expires_on: "2026-12-31",
    max_configs: 3,
    configs_count: 1,
    ...over,
  };
}

export const SERVERS: MyServer[] = [
  { id: 10, name: "nl-1", containers: [{ container: "amnezia-awg2", title: "AmneziaWG" }], load: "low", recommended: true },
];

export function err(status: number, code: string) {
  return HttpResponse.json({ code, message: code }, { status });
}

/** A signed-in user with the given profile and configs. */
export function userSession(profile: Me, configs: Config[], servers: MyServer[] = SERVERS) {
  server.use(
    http.get("/api/auth/session", () => HttpResponse.json({ role: "user", id: profile.id, login: profile.login })),
    http.get("/api/me", () => HttpResponse.json(profile)),
    http.get("/api/me/servers", () => HttpResponse.json(servers)),
    http.get("/api/me/configs", () => HttpResponse.json(configs)),
  );
}

export function adminSession() {
  server.use(http.get("/api/auth/session", () => HttpResponse.json({ role: "admin", id: 1, login: "admin" })));
}
