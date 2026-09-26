import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AdminUser,
  AdminUserDetail,
  AuditEntry,
  Config,
  Job,
  Me,
  MyServer,
  ServerInfo,
  TrafficReport,
} from "./types";

export const keys = {
  me: ["me"] as const,
  myServers: ["me", "servers"] as const,
  myConfigs: ["me", "configs"] as const,
  users: (params: string) => ["admin", "users", params] as const,
  user: (id: number) => ["admin", "user", id] as const,
  servers: ["admin", "servers"] as const,
  server: (id: number) => ["admin", "server", id] as const,
  orphans: ["admin", "orphans"] as const,
  traffic: (params: string) => ["admin", "traffic", params] as const,
  audit: ["admin", "audit"] as const,
};

export const useMe = () => useQuery({ queryKey: keys.me, queryFn: () => api<Me>("/api/me") });
export const useMyServers = () => useQuery({ queryKey: keys.myServers, queryFn: () => api<MyServer[]>("/api/me/servers") });
export const useMyConfigs = () => useQuery({ queryKey: keys.myConfigs, queryFn: () => api<Config[]>("/api/me/configs") });

export const useUsers = (params: URLSearchParams) =>
  useQuery({ queryKey: keys.users(params.toString()), queryFn: () => api<AdminUser[]>(`/api/admin/users?${params}`) });
export const useUser = (id: number) =>
  useQuery({ queryKey: keys.user(id), queryFn: () => api<AdminUserDetail>(`/api/admin/users/${id}`) });
export const useServers = () => useQuery({ queryKey: keys.servers, queryFn: () => api<ServerInfo[]>("/api/admin/servers") });
export const useServer = (id: number) =>
  useQuery({ queryKey: keys.server(id), queryFn: () => api<ServerInfo>(`/api/admin/servers/${id}`) });
export const useInstallable = () =>
  useQuery({
    queryKey: ["admin", "installable"],
    queryFn: () => api<{ container: string; title: string }[]>("/api/admin/servers/installable"),
    staleTime: Infinity,
  });
export const useOrphans = () =>
  useQuery({ queryKey: keys.orphans, queryFn: () => api<Config[]>("/api/admin/configs?orphan=true") });
export const useTraffic = (params: URLSearchParams) =>
  useQuery({ queryKey: keys.traffic(params.toString()), queryFn: () => api<TrafficReport>(`/api/admin/traffic?${params}`) });
export const useAudit = () =>
  useQuery({ queryKey: keys.audit, queryFn: () => api<AuditEntry[]>("/api/admin/audit?limit=100") });

/** A mutation that refreshes the given queries (by key prefix) when it succeeds. */
export function useAction<TVars = void, TResult = unknown>(
  fn: (vars: TVars) => Promise<TResult>,
  invalidate: QueryKey[],
  onSuccess?: (result: TResult, vars: TVars) => void,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (result, vars) => {
      await Promise.all(invalidate.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
      onSuccess?.(result, vars);
    },
  });
}

export function fetchJob(id: number) {
  return api<Job>(`/api/jobs/${id}`);
}
