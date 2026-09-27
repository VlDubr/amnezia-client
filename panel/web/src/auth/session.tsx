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

/** One sign-in page for every account: the server tells which role it has. */
export function loginPath(next?: string) {
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

export function homePath(role: Role) {
  return role === "admin" ? "/admin" : "/";
}

/** The page is only a convenience: every API call is checked against the account's role on the server. */
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
  if (!data) {
    return <Navigate to={loginPath(location.pathname + location.search)} replace />;
  }
  if (data.role !== role) {
    return <Navigate to={homePath(data.role)} replace />;
  }
  return <>{children}</>;
}

/** Where to go after a successful sign in: the `next` path if it belongs to the role's own area on this site. */
export function nextPath(search: string, role: Role): string {
  const next = new URLSearchParams(search).get("next");
  const home = homePath(role);
  // "//host" and "/\host" would leave the site.
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return home;
  const isAdminPath = next === "/admin" || next.startsWith("/admin/");
  return isAdminPath === (role === "admin") ? next : home;
}
