import { Navigate, type RouteObject } from "react-router";
import { RequireRole } from "./auth/session";
import { AppLayout } from "./components/AppLayout";
import AdminLoginPage from "./pages/admin/AdminLoginPage";
import AuditPage from "./pages/admin/AuditPage";
import OrphansPage from "./pages/admin/OrphansPage";
import ServerPage from "./pages/admin/ServerPage";
import ServersPage from "./pages/admin/ServersPage";
import UserPage from "./pages/admin/UserPage";
import UsersPage from "./pages/admin/UsersPage";
import AccountPage from "./pages/user/AccountPage";
import DashboardPage from "./pages/user/DashboardPage";
import InvitePage from "./pages/user/InvitePage";
import LoginPage from "./pages/user/LoginPage";

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  { path: "/invite", element: <InvitePage /> },
  { path: "/admin/login", element: <AdminLoginPage /> },
  {
    path: "/",
    element: (
      <RequireRole role="user">
        <AppLayout area="user" />
      </RequireRole>
    ),
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "account", element: <AccountPage /> },
    ],
  },
  {
    path: "/admin",
    element: (
      <RequireRole role="admin">
        <AppLayout area="admin" />
      </RequireRole>
    ),
    children: [
      { index: true, element: <Navigate to="users" replace /> },
      { path: "users", element: <UsersPage /> },
      { path: "users/:id", element: <UserPage /> },
      { path: "servers", element: <ServersPage /> },
      { path: "servers/:id", element: <ServerPage /> },
      { path: "orphans", element: <OrphansPage /> },
      { path: "audit", element: <AuditPage /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
];
