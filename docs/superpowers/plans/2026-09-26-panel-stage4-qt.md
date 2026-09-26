# Amnezia Panel — Stage 4: Admin Screens in the Qt Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** From the AmneziaVPN desktop and mobile app, a panel administrator can manage users (А.1, А.4–А.6), their configs (А.2) and see traffic (А.7) through the panel REST API.

**Architecture:**
- **Entry point.** A new "Amnezia Panel" entry in Settings opens a sign-in page (panel URL, login, password). After that come a user list and a user page.
- **Networking.** `PanelApiClient` (core, no UI) sends JSON requests with `QNetworkAccessManager` and a Bearer token, and turns `{code, message}` into errors.
- **UI layer.** `PanelUiController` exposes the data to QML as `QVariantList`/`QVariantMap` properties, following the existing `*UiController` pattern. It is registered in `CoreController`.
- **Storage.** The panel URL and token are kept in `SecureQSettings` through `SecureAppSettingsRepository`.

**Ruling vs spec §10:** the spec proposed a new `ConfigType::PanelAdmin` server type. This plan uses a separate Settings entry instead.
- A panel is not a VPN server: putting it in the servers list would touch the connection, servers-model and home-page logic everywhere.
- Cost if wrong: the panel does not appear in the servers list.

**Tech Stack:** Qt 6.10 (C++17, QML), existing Amnezia controls (`LabelWithButtonType`, `BasicButtonType`, `TextFieldWithHeaderType`, `CopyButton`-style actions).

**Spec:** `docs/superpowers/specs/2026-09-26-amnezia-panel-design.md` (§10)

## Global Constraints

- Follow AGENTS.md:
  - register every new `.cpp`/`.h` in `client/cmake/sources.cmake`;
  - add every QML page to `ui/qml/qml.qrc`;
  - add pages to `ui/utils/pageEnum.h` (the enum name is the QML file name);
  - register controllers in `CoreController`.
- Use `.clang-format`: WebKit style, 120 columns.
- No new dependencies.
- The token is never logged.

## Tasks

### Task 1: PanelApiClient + settings storage
- Files:
  - `core/controllers/panel/panelApiClient.{h,cpp}`;
  - `core/repositories/secureAppSettingsRepository.{h,cpp}` (add `panelUrl`/`setPanelUrl` and `panelToken`/`setPanelToken`);
  - `client/cmake/sources.cmake`.
- `void request(const QString &method, const QString &path, const QJsonObject &body, std::function<void(int status, QJsonDocument, QString errorCode, QString errorMessage)>)`.
- Verify: the client builds.

### Task 2: PanelUiController
- File: `ui/controllers/panel/panelUiController.{h,cpp}`, registered in `CoreController` as `PanelController`.
- Properties:
  - `isSignedIn`, `busy`, `errorText`;
  - `users` (list), `user` (map), `servers` (list), `inviteKey`, `share` (map with `vpn_key`, `native` and `qr_svg`).
- Invokables:
  - `signIn(url, login, password)`, `signOut()`;
  - `loadUsers(query)`, `createUser(name, maxConfigs, expiresOn, note)`, `loadUser(id)`, `saveUser(id, name, maxConfigs, expiresOn, note)`;
  - `blockUser`, `unblockUser`, `reissueInvite`, `deleteUser`;
  - `loadServers()`, `issueConfig(userId, serverId, container)`;
  - `showConfig(id)`, `blockConfig(id)`, `unblockConfig(id)`, `deleteConfig(id)`.
- Verify: the client builds.

### Task 3: QML pages
- Files:
  - `ui/qml/Pages2/PagePanelLogin.qml`, `PagePanelUsers.qml`, `PagePanelUser.qml`;
  - `pageEnum.h`, `qml.qrc`;
  - a Settings entry in `PageSettings.qml`.
- The user page shows:
  - status, limit and last day with a save button;
  - block/unblock, the invite key (shown once, with copy), delete;
  - traffic totals;
  - configs with show (vpn key with copy, QR from `qr_svg`), block/unblock and delete;
  - "issue config" (server and protocol list).
- Verify:
  - the client builds;
  - `qmllint` on the new pages if available;
  - manual run against the e2e backend (`panel/backend/tests/e2e_server.py`) when a display is available.

### Task 4: Docs
- `AGENTS.md` and `panel/README.md`: where the Qt admin screens are and how to use them.
