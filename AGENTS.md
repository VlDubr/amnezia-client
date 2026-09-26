# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project

AmneziaVPN is a cross-platform VPN client (Windows, macOS, Linux, Android, iOS) written in C++17 / Qt 6 with a QML UI.
It works in two modes:

- **Self-hosted**: the user gives SSH credentials for their own server, and the client installs each VPN protocol there as a Docker container, then connects to it.
- **API / subscription**: the client gets configs from the Amnezia API gateway (free and premium services, in-app purchases).

Protocols: OpenVPN (plain, over Cloak, over Shadowsocks), WireGuard, AmneziaWG (AWG), Xray (VLESS/Reality, SS over Xray), IKEv2 (Windows only). Additional self-hosted services: SFTP, SOCKS5, Tor website, MTProxy, Telemt, TProxy.

License: GPLv3. The default branch is `dev`.

## Build

Requirements:

- CMake 3.25 or later.
- Qt 6.10 or later, with Core5Compat and Qt Remote Objects.
- Conan 2. It must be in `PATH`. On macOS, Homebrew or a `.venv` in the repo root also works.
- The platform toolchain:
  - Windows: MSVC 2022.
  - macOS and iOS: Xcode.
  - Linux: gcc and make.
  - Android: Android SDK/NDK and Ninja.

Initialize submodules first:

```bash
git submodule update --init --recursive
```

Build scripts (the default output folder is `deploy/build`, and the default build type is `Release`):

```bash
deploy/build.sh                        # host platform
deploy/build.sh -t android --aab       # targets: linux, macos, macos-ne, ios, android
deploy/build.sh -t android --aab --play  # Google Play flavor (Play Billing)
deploy/build.sh --installer all        # also build installers (IFW / productbuild)
deploy/build.sh -f                     # delete the build folder before configure
deploy/build.sh -h                     # all options
```

```bat
deploy\build.bat
deploy\build.bat --installer ifw --installer wix
```

The scripts find Qt in `~/Qt` or `/opt/Qt`. You can override the paths with `QT_ROOT_PATH`, `QT_INSTALL_DIR`, `QIF_ROOT_PATH`, `ANDROID_HOME`, `CMAKE_BUILD_TYPE` and `JOBS`.

To use plain CMake (or Qt Creator), point it at a Qt kit:

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH=<Qt>/<version>/<kit> -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
```

What configure does:

- The first configure is slow. `cmake/recipes_bootstrap.cmake` runs `conan export` for every recipe in `recipes/` and adds the `amnezia` Conan remote, which serves prebuilt packages.
- `cmake/conan_provider.cmake` then runs `conan install --build=missing` from `find_package`. `cmake/platform_settings.cmake` sets per-platform Conan arguments and the deployment targets.
- `-DPREBUILTS_ONLY=ON` stops after the dependencies are installed. The CI `Bake-Prebuilts-*` jobs use this.
- `-DMACOS_NE=ON` builds the macOS app as a Network Extension app. That build has no privileged service.

`client/CMakeLists.txt` compiles environment variables into the binary as defines: `PROD_AGW_PUBLIC_KEY`, `PROD_S3_ENDPOINT`, `FALLBACK_S3_ENDPOINT`, `DEV_AGW_PUBLIC_KEY`, `DEV_AGW_ENDPOINT`, `DEV_S3_ENDPOINT`, `FREE_V2_ENDPOINT` and `PREM_V1_ENDPOINT`. CI supplies them from secrets. In a local build they are empty, so the API gateway features have no endpoints. Self-hosted mode does not use them.

To produce the Xcode or Android Studio projects, configure with CMake first. Then open `AmneziaVPN.xcodeproj` or `<build-dir>/client/android-build`. Edits made there are saved in the build folder, so copy them back to the source tree.

### Tests and verification

The application code has no unit test suite. The only tests are the ones bundled with the vendored `client/3rd/QJsonStruct`. To verify a change, build every platform the change affects and run the app. CI (`.github/workflows/deploy.yml`) builds Linux, Windows, macOS, macOS NE, iOS and Android on each push.

### Formatting

`.clang-format` is WebKit-based:

- 120 columns.
- Braces go on a new line after classes, structs, functions and namespaces.
- Braces stay on the same line after control statements.

`.clang-format-ignore` excludes vendored and platform code: `client/3rd`, `client/mozilla`, `client/daemon`, `client/platforms/*`, `client/android`, `client/ios`, `client/server_scripts`, `service/src` and others. Do not reformat those directories.

Commit messages use Conventional Commits, and the merged PR number is appended, for example `feat: add TProxy (#3034)` or `fix: ...` or `chore: bump version (#3120)`.

## Architecture

The top-level `CMakeLists.txt` always adds `client/`. It adds `service/` only on desktop (not iOS, not Android, not `MACOS_NE`). `ipc/` and `common/` (logger, crypto) are shared between the two.

### Desktop: two processes

- `AmneziaVPN` (`client/`) runs as the normal user. It owns the UI, the config storage and the SSH provisioning.
- `AmneziaVPN-service` (`service/server/`) runs as root or SYSTEM. It handles routes, the kill switch and firewall (WFP on Windows, pf on macOS), DNS, TUN devices, TAP drivers, and Xray through `xray-bindings`.

The two processes talk over two channels:

1. **Qt Remote Objects IPC.**
   - `ipc/ipc_interface.rep` defines `IpcInterface`: routes, kill switch, DNS resolvers, TUN, `xrayStart`/`xrayStop`, network check. `ipc/ipc_process_interface.rep` lets the service start privileged processes, which OpenVPN uses.
   - The client calls the service through `IpcClient::withInterface(...)` (`client/core/utils/ipcClient.h`). The service side is `ipc/ipcserver.*` and `ipc/ipcserverprocess.*`.
   - `repc` generates code from the `.rep` files, so a change to a `.rep` file changes both processes.
2. **WireGuard/AWG daemon, based on the Mozilla VPN daemon.** The client's `WireguardProtocol` uses `client/mozilla/localsocketcontroller` to send JSON over a local socket to `client/daemon/Daemon`. The per-OS parts are in `client/platforms/<os>/daemon/`.

Several directories under `client/` are compiled into the service, not the client: `client/daemon`, parts of `client/mozilla`, and `client/platforms/*/daemon`. `service/server/CMakeLists.txt` lists them. On Windows, the service also starts itself with the `tunneldaemon` argument (`WindowsDaemonTunnel`) to host the WireGuard tunnel service.

### Mobile and the macOS Network Extension

- **Android**: the Kotlin/Java code is in `client/android/`. It contains `AmneziaVpnService`, one module per protocol (`awg`, `openvpn`, `wireguard`, `xray`) and billing with `play` and `oss` flavors. C++ reaches it through `AndroidController` (`client/platforms/android`, JNI) and `AndroidVpnProtocol`.
- **iOS and macOS NE**: the Swift and ObjC++ code is in `client/platforms/ios`. The Network Extension targets are `client/ios/networkextension` and `client/macos/networkextension`. They use WireGuardKit (awg-apple), openvpnadapter and hev-socks5-tunnel. These builds have no service and no IPC.

### Client layering (`client/`)

- `main.cpp` runs the migrations and then starts `AmneziaApplication`. On desktop, `AmneziaApplication` also enforces a single instance through a local socket.
- `CoreController` (`core/controllers/coreController.*`) builds the whole object graph in this order: repositories, core controllers, models, UI controllers. It exposes the objects to QML as context properties. `CoreSignalHandlers` connects their signals. A new controller or model must be registered here.
- `core/repositories/` contains `SecureServersRepository` and `SecureAppSettingsRepository`. They persist data through `SecureQSettings`, which is QSettings encrypted with a key kept in qtkeychain.
- `core/models/` contains the typed config objects: `ServerDescription`, `ContainerConfig`, `ProtocolConfig`, the per-protocol configs in `protocols/`, the API configs in `api/` and the self-hosted configs in `selfhosted/`. `core/utils/serialization/` parses share links (vless, vmess, ss, trojan).
- `core/controllers/` holds the business logic, with no UI. It includes connection, servers, settings, split tunneling, allowed DNS and updates. `selfhosted/` holds install, import, export and users. `api/` holds subscription, store purchase, services catalog and news. `gatewayController` talks to the Amnezia API gateway.
- `ui/controllers/` (`*UiController`) and `ui/models/` (Qt item models) are thin adapters that QML uses on top of the core controllers. `ui/controllers/qml/pageController` handles navigation, and the page enum is in `ui/utils/pageEnum.h`.
- `ui/qml/`: the entry point is `main2.qml` and the pages are in `Pages2/`. Every QML file must be listed in `ui/qml/qml.qrc`.
- `vpnConnection.cpp` and `core/protocols/` handle connecting:
  - On desktop, `VpnProtocol::factory` maps a `DockerContainer` to its implementation: `OpenVpnProtocol`, `WireguardProtocol` (also used for AWG), `XrayProtocol`, or `Ikev2Protocol` on Windows.
  - Android and iOS use their platform protocol.
  - `VpnConnection` also sets routes and IP split tunneling through IPC.

### Self-hosted provisioning

Each protocol or service is a Docker container that the client installs on the user's server over SSH (libssh; `core/utils/selfhosted/sshClient`, `sshSession`, `scriptsRegistry`). A new container type touches all of the layers below. Commit `dedfc08c` ("feat: add TProxy") shows a complete example.

- Enums: `core/utils/containerEnum.h`, `core/utils/protocolEnum.h` and `core/utils/containers/containerUtils.cpp`.
- Constants: `core/utils/constants/` (`configKeys.h`, `protocolConstants.h`).
- Shell scripts in `client/server_scripts/<proto>/` (Dockerfile, `configure_container.sh`, `run_container.sh`, `start.sh`). Register them in `server_scripts/serverScripts.qrc` and in `scriptsRegistry`.
- The installer: `core/installers/<proto>Installer`. For VPN protocols, also the client config generator: `core/configurators/<proto>Configurator`.
- The config model: `core/models/protocols/<proto>ProtocolConfig`. Wire it into `containerConfig` and `protocolConfig`.
- The UI model (`ui/models/protocols/` or `ui/models/services/`), `containersModel`, `protocolsModel`, the `installController` and `installUiController` logic, and a QML page in `Pages2/` added to `qml.qrc`.

### Registering source files

`client/cmake/sources.cmake` globs only these directories: `core/configurators`, `core/models/**`, `ui/models/{.,protocols,services,utils,api}` and `ui/controllers/{.,api,qml,selfhosted}`.

Add every other file by hand to `sources.cmake` or to the platform file (`client/cmake/android.cmake`, `ios.cmake`, `macos.cmake` or `macos_ne.cmake`). This includes `core/controllers`, `core/installers`, `core/protocols`, `core/repositories`, `core/utils` and `platforms/`. Run configure again after you add files.

### Dependencies

- Native dependencies come from Conan (`conanfile.py`), and custom recipes are in `recipes/`. The set depends on the platform:
  - Desktop with the service: awg-go or awg-windows, openvpn, amnezia-xray-bindings, tun2socks, plus wintun, tap-windows6 and win-split-tunnel on Windows.
  - Network Extension builds: awg-apple, openvpnadapter, hev-socks5-tunnel.
  - Android: awg-android, amnezia-libxray, openvpn-pt-android.
  - All platforms use `libssh/…@amnezia`, openssl and zlib.
- Git submodules in `client/3rd/`: qtkeychain, SortFilterProxyModel, amneziawg-apple and qtgamepad. QJsonStruct and qrcodegen are vendored.

### Branding

`client/cmake/branding/*.cmake` defines the `CLIENT_*` cache variables: target, application and service names, organization, keychain name, QML entry point and pages prefix, and translation prefix. They are overridable. In code, use the generated macros (`APPLICATION_NAME`, `ORGANIZATION_NAME`, `APP_INSTANCE_NAME`, …) and do not hardcode "AmneziaVPN".

### Translations and versioning

- The translation files are in `client/translations/*.ts`, and `AMNEZIAVPN_TS_FILES` in `client/CMakeLists.txt` lists them. To add a language, add its `.ts` file there, then update `ui/models/languageModel.*` and `ui/controllers/languageUiController.cpp`. Commit `2a8242d3` shows an example.
- The app version is `AMNEZIAVPN_VERSION` in the root `CMakeLists.txt`. Increase `APP_ANDROID_VERSION_CODE` by 2 for each release, because two Play Store variants are published.

## Panel (`panel/`)

`panel/` holds a separate web application, Amnezia Panel. It manages users, invite keys, servers and configs on self-hosted Amnezia servers over SSH. It is not part of the CMake build. The design is `docs/superpowers/specs/2026-09-26-amnezia-panel-design.md` and the operator guide is `panel/README.md`.

- **`panel/backend`** is Python 3.12+ with FastAPI, SQLAlchemy 2 (async), Alembic, asyncssh and APScheduler, managed with uv.
  - Run `uv run pytest` for unit and API tests. It starts PostgreSQL through testcontainers, so Docker must be running.
  - Run `uv run pytest -m integration` to build real `amnezia-awg2`/`amnezia-wireguard` containers from `client/server_scripts`. It drives them through an SSH test host and connects a real AmneziaWG client.
- **Source of truth.** The PostgreSQL database holds the desired state.
  - A change such as block, unblock, delete or expiry only updates rows and enqueues a `reconcile` job (`app/services/sync.py`).
  - `app/services/reconcile.py` then makes each server match that state. Clients unknown to the panel are imported as configs without an owner and are never removed.
- **Protocol drivers.** Each protocol has a driver in `app/drivers/` that implements `Driver` (`app/drivers/base.py`).
  - A driver must issue the same server commands as the Qt client: `sudo docker exec`, `wg/awg syncconf`, and the same file paths.
  - Before changing a driver, check the matching configurator or installer in `client/core/`.
  - Scripts are read from `client/server_scripts`. The backend Docker image is built from the repository root and copies them in.
  - Drivers exist for AWG, WireGuard, Xray, OpenVPN, SOCKS5, Telemt, MTProxy and IKEv2. Register a new driver in `_MODULES` in `app/drivers/base.py`. `apply()` distinguishes a reversible block from a permanent `revoked` removal.
  - `Remote.container_exec` uploads the script to a file inside the container before running it, like the Qt client's `runContainerScript`. Do not feed scripts through stdin: tools such as certutil read stdin and would consume the rest of the script.
- **Jobs.** Jobs are stored in the `jobs` table and run by the in-process worker (`app/jobs/`).
  - All SSH work on one server is serialized by `server_lock` (a PostgreSQL advisory lock).
  - The scheduler only enqueues jobs.
- **Qt client admin screens.**
  - Settings → "Amnezia Panel" opens `PagePanelLogin`/`PagePanelUsers`/`PagePanelUser`.
  - `PanelApiClient` (`client/core/controllers/panel/`) calls the panel REST API with the admin token. The token and the panel URL are stored in `SecureAppSettingsRepository`.
  - `PanelUiController` (`client/ui/controllers/panel/`) is the QML context property `PanelController`.
- **Deploy.** `panel/deploy` holds the docker compose stack: Caddy, backend, PostgreSQL and backups. `PANEL_MASTER_KEY` encrypts secrets in the database.
