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
export type LoadLevel = "low" | "medium" | "high" | "unknown";

export type MyServer = { id: number; name: string; containers: ContainerRef[]; load: LoadLevel; recommended: boolean };

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
  load: LoadLevel;
  load_pct: number | null;
  bandwidth_mbps: number | null;
  expected_clients: number | null;
  metrics_iface: string | null;
};

export type LoadPoint = { ts: string; cpu: number | null; mem: number | null; rx: number | null; tx: number | null; clients: number | null };

type MetricWindow = { avg: number | null; points: number; last_at: string | null };

export type ServerLoad = {
  specs: {
    cpu_model?: string | null; cores?: number | null; mem_bytes?: number | null; disk_bytes?: number | null;
    os?: string | null; kernel?: string | null; iface?: string | null; link_mbps?: number | null; uptime_s?: number | null;
  };
  specs_at: string | null;
  current: {
    ts: string; cpu: number | null; mem: number | null; disk: number | null; load1: number | null;
    rx: number | null; tx: number | null; clients: number | null; iface: string | null;
  } | null;
  window: { cpu: MetricWindow; mem: MetricWindow; net: MetricWindow; clients: MetricWindow };
  level: LoadLevel;
  utilisation: number | null;
  capacity: { bandwidth_mbps: number | null; expected_clients: number | null; metrics_iface: string | null };
  hints: { link_mbps: number | null; peak_mbps_7d: number | null };
  series: LoadPoint[];
  peaks: { cpu: number | null; mem: number | null; net: number | null; clients: number | null };
  recommendations: { code: string; severity: "warning" | "info"; params: Record<string, unknown> }[];
  untracked_protocols: string[];
  metrics_error: string | null;
  metrics_error_at: string | null;
};

export type Job = { id: number; kind: string; status: "queued" | "running" | "done" | "failed"; attempts: number; last_error: string | null };

export type TrafficReport = { rows: { day: string; server_id: number; rx: number; tx: number }[]; total: Traffic };

export type AuditEntry = { id: number; actor: string; action: string; target: string; details: Record<string, unknown>; ts: string };
