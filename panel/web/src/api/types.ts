// Shapes returned by the panel backend (panel/backend/app/api/presenters.py and routers).

export type Role = "admin" | "user";
export type UserStatus = "active" | "blocked" | "expired" | "deleting";
export type ConfigStatus = "active" | "blocked" | "inactive" | "deleting";
export type Traffic = { rx: number; tx: number };

export type SessionInfo = { role: Role; id: number; login: string };

export type Me = {
  id: number;
  display_name: string;
  login: string;
  status: UserStatus;
  expires_on: string | null;
  max_configs: number;
  configs_count: number;
};

export type ContainerRef = { container: string; title: string };
export type MyServer = { id: number; name: string; containers: ContainerRef[] };

export type Export = { vpn_key: string; native: string; native_filename: string; qr_svg: string };

export type Config = {
  id: number;
  name: string;
  user_id: number | null;
  server_id: number;
  server_name: string;
  container: string;
  protocol: string;
  client_id: string;
  status: ConfigStatus;
  blocked_by: "user" | "admin" | null;
  can_render: boolean | null;
  traffic: Traffic;
  created_at: string;
  export?: Export | null;
};

export type AdminUser = {
  id: number;
  display_name: string;
  note: string;
  login: string | null;
  registered: boolean;
  status: UserStatus;
  blocked_by: "admin" | "expiry" | null;
  expires_on: string | null;
  max_configs: number;
  configs_count: number;
  traffic_total: Traffic;
  created_at: string;
};

export type AdminUserDetail = AdminUser & {
  configs: Config[];
  traffic_by_server: { server_id: number; server_name: string; rx: number; tx: number }[];
};

export type ServerInfo = {
  id: number;
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  enabled_for_users: boolean;
  host_key: string | null;
  imported_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  created_at: string;
  configs_count: number;
  containers: { container: string; title: string; port: string | null }[];
};

export type Job = { id: number; kind: string; status: "queued" | "running" | "done" | "failed"; attempts: number; last_error: string | null };

export type TrafficReport = { rows: { day: string; server_id: number; rx: number; tx: number }[]; total: Traffic };

export type AuditEntry = { id: number; actor: string; action: string; target: string; details: Record<string, unknown>; ts: string };
