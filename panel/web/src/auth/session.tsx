import { Center, Loader } from "@mantine/core";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { ApiError, api, setUnauthorizedHandler } from "../api/client";
import type { Role, SessionInfo } from "../api/types";

export const SESSION_KEY = ["session"] as const;

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => {
      try {
        return await api<SessionInfo>("/api/auth/session");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
  });
}

/** Loads the new session into the cache before navigating into a protected area.

Invalidating is not enough: without mounted observers the cached value (null after a sign out)
would be used by the route guard before any refetch finishes.
*/
export async function loadSession(queryClient: QueryClient) {
  queryClient.setQueryData(SESSION_KEY, await api<SessionInfo>("/api/auth/session"));
}

/** Drops the cached session on any 401 so route guards send the visitor to a login page. */
export function SessionBridge() {
  const queryClient = useQueryClient();
  useEffect(() => {
    setUnauthorizedHandler(() => queryClient.setQueryData(SESSION_KEY, null));
    return () => setUnauthorizedHandler(null);
  }, [queryClient]);
  return null;
}

export function loginPath(role: Role, next?: string) {
  const base = role === "admin" ? "/admin/login" : "/login";
  return next ? `${base}?next=${encodeURIComponent(next)}` : base;
}

export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { data, isPending } = useSession();
  const location = useLocation();
  if (isPending) {
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  }
  if (!data || data.role !== role) {
    return <Navigate to={loginPath(role, location.pathname + location.search)} replace />;
  }
  return <>{children}</>;
}

/** Where to go after a successful sign in: the `next` path if it belongs to the same area. */
export function nextPath(search: string, role: Role): string {
  const next = new URLSearchParams(search).get("next");
  const home = role === "admin" ? "/admin" : "/";
  if (!next || !next.startsWith("/") || next.startsWith("//")) return home;
  const isAdminPath = next === "/admin" || next.startsWith("/admin/");
  return isAdminPath === (role === "admin") ? next : home;
}
