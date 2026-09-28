import type { ServerLoad } from "../src/api/types";

export const EMPTY_LOAD: ServerLoad = {
  specs: {}, specs_at: null, current: null,
  window: {
    cpu: { avg: null, points: 0, last_at: null }, mem: { avg: null, points: 0, last_at: null },
    net: { avg: null, points: 0, last_at: null }, clients: { avg: null, points: 0, last_at: null },
  },
  level: "unknown", utilisation: null,
  capacity: { bandwidth_mbps: null, expected_clients: null, metrics_iface: null },
  hints: { link_mbps: null, peak_mbps_7d: null },
  series: [], peaks: { cpu: null, mem: null, net: null, clients: null },
  recommendations: [], untracked_protocols: [], metrics_error: null, metrics_error_at: null,
};
