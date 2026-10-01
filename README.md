# Amnezia VPN

> [!IMPORTANT]
> **This is a fork of [amnezia-vpn/amnezia-client](https://github.com/amnezia-vpn/amnezia-client) with an added administrator web panel (Amnezia Panel).**
> The panel shares VPN access among many users: the administrator creates users, who register with an invite key and get their configs in a personal web cabinet. See [What is added in this fork](#what-is-added-in-this-fork). The rest of this README describes the original AmneziaVPN client.

### _The best client for self-hosted VPN_

[![Build Status](https://github.com/amnezia-vpn/amnezia-client/actions/workflows/deploy.yml/badge.svg?branch=dev)](https://github.com/amnezia-vpn/amnezia-client/actions/workflows/deploy.yml?query=branch:dev)
[![Gitpod ready-to-code](https://img.shields.io/badge/Gitpod-ready--to--code-blue?logo=gitpod)](https://gitpod.io/#https://github.com/amnezia-vpn/amnezia-client)

### English | [Русский](README_RU.md)


[Amnezia](https://amnezia.org?utm_source=github&utm_campaign=amnezia_website-readme-en) is an open-source VPN client, with a key feature that enables you to deploy your own VPN server on your server.

[![Image](https://github.com/amnezia-vpn/amnezia-client/blob/dev/metadata/img-readme/uipic4.png)](https://amnezia.org)

### [Website](https://amnezia.org?utm_source=github&utm_campaign=amnezia_website-readme-en) | [Alt website link](https://storage.googleapis.com/amnezia/amnezia.org?utm_source=github&utm_campaign=amnezia_website-readme-en-mirror) | [Documentation](https://docs.amnezia.org) | [Troubleshooting](https://docs.amnezia.org/troubleshooting)

> [!TIP]
> If the [Amnezia website](https://amnezia.org?utm_source=github&utm_campaign=amnezia_website-readme-en) is blocked in your region, you can use an [Alternative website link](https://storage.googleapis.com/amnezia/amnezia.org?utm_source=github&utm_campaign=amnezia_website-readme-en-mirror).

<a href="https://amnezia.org/en/downloads?utm_source=github&utm_campaign=amnezia_button-readme-en"><img src="https://github.com/amnezia-vpn/amnezia-client/blob/dev/metadata/img-readme/download-website.svg" width="150" style="max-width: 100%; margin-right: 10px"></a>
<a href="https://storage.googleapis.com/amnezia/amnezia.org?m-path=/en/downloads&utm_source=github&utm_campaign=amnezia_button-readme-en-mirrow"><img src="https://github.com/amnezia-vpn/amnezia-client/blob/dev/metadata/img-readme/download-alt.svg" width="150" style="max-width: 100%;"></a>

[All releases](https://github.com/amnezia-vpn/amnezia-client/releases)

<br/>

<a href="https://www.testiny.io"><img src="https://github.com/amnezia-vpn/amnezia-client/blob/dev/metadata/img-readme/testiny.png" height="28px"></a>

## What is added in this fork

### Amnezia Panel: a web panel that shares VPN access among users

A separate web application in [`panel/`](panel/README.md). It runs on its own VPS with `docker compose` and manages your Amnezia servers over SSH with the same scripts the app uses. Servers already set up with AmneziaVPN can be added to the panel together with their existing clients.

**Administrators** (in the web admin area and in the AmneziaVPN app):

- create users rather than single configs; each new user gets a one-time invite key for registration;
- group servers into one cluster: a user can use every server of the cluster;
- set a config limit and an access period; when the period ends, the user is blocked automatically;
- block a user (all their configs stop working but are kept) and unblock them (the configs work again);
- delete a user together with their configs;
- show and copy an existing config again (`vpn://` key, file, QR code) to send it to the user;
- see the traffic of each user and each server;
- add servers, install protocols on them and import existing clients;
- see each server's hardware, current load, 24-hour and 7-day charts and recommendations.

**Users** (web cabinet):

- sign in with a login and password;
- register with the "Enter a key to register" button, choosing a login and a strong password; the key works once;
- see the available servers with their load (low, medium or high) and the recommended one, and how many configs they can still create;
- create configs under a name of their choice, and block, delete and copy them again; the connection in the AmneziaVPN app and the downloaded file get that name (the file name transliterated to Latin, e.g. `Moy_telefon.conf`); an Xray config also downloads as a JSON file, as in AmneziaVPN;
- change their password.

**Protocols:** AmneziaWG, WireGuard, XRay (VLESS REALITY), OpenVPN, SOCKS5, Telemt, MTProxy and IKEv2 (only when already installed from the app). See the table in [panel/README.md](panel/README.md#supported-protocols).

**Parts:**

- `panel/backend`: Python, FastAPI, PostgreSQL; a background job queue keeps the servers in line with the database (blocks, access expiry, traffic) and samples the server load once a minute;
- `panel/web`: the React web UI (Russian and English);
- `panel/deploy`: a `docker compose` stack with Caddy (HTTPS, including a self-signed certificate for a server without a domain), the backend, PostgreSQL and daily backups.

### Changes in the AmneziaVPN app

- A new section, Settings → **Amnezia Panel**: panel administrator sign-in, the user list, creating a user with an invite key, the config limit and access period, blocking and deleting, issuing configs with a QR code, and traffic per server.
- The panel administrator token is stored encrypted and is left out of the app's settings backups.
- A panel with a self-signed certificate is trusted by its SHA-256 fingerprint, confirmed on the first sign-in.
- Russian and Ukrainian translations of the new screens.

### Install and try it out

How to install the panel on a VPS and how to try it locally without VPN servers: [panel/README.md](panel/README.md).

## Features

- Very easy to use - enter your IP address, SSH login, password and Amnezia will automatically install VPN docker containers to your server and connect to the VPN.
- Classic VPN-protocols: OpenVPN, WireGuard and IKEv2 protocols.
- Protocols with traffic Masking (Obfuscation): OpenVPN over [Cloak](https://github.com/cbeuw/Cloak) plugin, Shadowsocks (OpenVPN over Shadowsocks), [AmneziaWG](https://docs.amnezia.org/documentation/amnezia-wg/) and XRay.
- Split tunneling support - add any sites to the client to enable VPN only for them or add Apps (only for Android and Desktop).
- Windows, MacOS, Linux, Android, iOS releases.
- Support for AmneziaWG protocol configuration on [Keenetic beta firmware](https://docs.keenetic.com/ua/air/kn-1611/en/6319-latest-development-release.html#UUID-186c4108-5afd-c10b-f38a-cdff6c17fab3_section-idm33192196168192-improved).

## Links

- [https://amnezia.org](https://amnezia.org/?utm_source=github&utm_campaign=amnezia_website-read) - Project website | [Alternative link (mirror)](https://storage.googleapis.com/amnezia/amnezia.org?utm_source=github&utm_campaign=amnezia_website-read)
- [https://docs.amnezia.org](https://docs.amnezia.org/?utm_source=github&utm_campaign=amnezia_website-read) - Documentation | [Alternative link (mirror)](https://storage.googleapis.com/amnezia/docs?utm_source=github&utm_campaign=amnezia_website-read)
- [https://www.reddit.com/r/AmneziaVPN](https://www.reddit.com/r/AmneziaVPN) - Reddit  
- [https://telegram.me/amnezia_vpn_en](https://telegram.me/amnezia_vpn_en) - Telegram support channel (English) 
- [https://telegram.me/amnezia_vpn_ir](https://telegram.me/amnezia_vpn_ir) - Telegram support channel (Farsi) 
- [https://telegram.me/amnezia_vpn_mm](https://telegram.me/amnezia_vpn_mm) - Telegram support channel (Myanmar)  
- [https://telegram.me/amnezia_vpn](https://telegram.me/amnezia_vpn) - Telegram support channel (Russian)
- [Get Premium for 6 or 12 months](https://storage.googleapis.com/amnezia/pay?utm_source=github&utm_campaign=ampay-read)

## Tech

AmneziaVPN uses several open-source projects to work:

- [OpenSSL](https://www.openssl.org/)
- [OpenVPN](https://openvpn.net/)
- [Qt](https://www.qt.io/)
- [LibSsh](https://libssh.org)
- [WireGuard](https://www.wireguard.com/)
- [Xray-core](https://xtls.github.io/en/)
- [Conan](https://conan.io/)
- and more...

## Help us with translations

Download the most actual translation files.

Go to ["Actions" tab](https://github.com/amnezia-vpn/amnezia-client/actions?query=is%3Asuccess+branch%3Adev), click on the first line.
Then scroll down to the "Artifacts" section and download "AmneziaVPN_translations".

Unzip this file.
Each *.ts file contains strings for one corresponding language.

Translate or correct some strings in one or multiple *.ts files and commit them back to this repository into the ``client/translations`` folder.
You can do it via a web-interface or any other method you're familiar with.

## Checking out the source code

Make sure to pull all submodules after checking out the repo.

```bash
git submodule update --init --recursive
```

## Hacking guide

Want to contribute? Welcome!

### Build requirements

* [`CMake`](https://cmake.org/download/)
* Compiler and underlying build system, depending on the target:
  - [Linux] Any of `make` and `gcc`
  - [Apple] [`Xcode`](https://developer.apple.com/xcode/) or [`Xcode command line tools`](https://developer.apple.com/xcode/)
  - [Windows] [`Visual Studio 2022`](https://aka.ms/vs/17/release/vs_community.exe) or [`VS 2022 Build Tools`](https://aka.ms/vs/17/release/vs_buildtools.exe)
  - [Android] [`Android SDK`](#installing-android-sdk) and [`Ninja`](https://ninja-build.org/)
* [`Qt 6.10+`](https://www.qt.io/download-open-source) with the following modules:
  - Core module for targeting platform (Desktop/Android/iOS)
  - Qt 5 Compatibility module
  - Qt Remote Objects
* [`Conan`](https://conan.io/downloads) package manager
  - On MacOS is enough just to use `homebrew` or install it in `.venv` in project root
  - Other systems must have it in `PATH`
* (Optional) Installer dependencies:
  - [Windows/Linux] [`Qt Installer Framework`](https://www.qt.io/download-open-source)
  - [Windows] [`WIX toolset`](https://github.com/wixtoolset/wix/releases)

### Building the project using scripts

* Run scripts located in `deploy` directory
* Basically, if dependencies are located in default installation paths, the scripts will find them automatically.
* If they differ, specify them using the following variables:
  - `QT_INSTALL_DIR` - Qt root installation folder
  - `QT_ROOT_PATH`   - Qt framework root directory
  - `QIF_ROOT_PATH`  - Qt Installer Framework root path
  - `ANDROID_HOME`   - Path to Android SDK root folder
  - and others. Check scripts for more

Unix-like:
```bash
# Build executables for the host platform
deploy/build.sh

# Or just
deploy/build.sh

# Build executables and installers for the host platform
deploy/build.sh --installer all

# Build Android APK and AAB
deploy/build.sh -t android --aab

# Call for help
deploy/build.sh -h
```

Windows:
```batch
:: Build executables for Windows
deploy/build.bat

:: Build executables with IFW installer for Windows
deploy/build.bat --installer ifw

:: Build executables with IFW and WIX installer for Windows
deploy/build.bat --installer ifw --installer wix

:: Or just
deploy/build.bat --installer all
```

### Developing the project in IDEs

* Basically, you can use any IDE that handles CMake and Qt kits properly to run configure and build steps, and to navigate through the code nicely. For example:
  - `Qt Creator`
  - `Visual Studio Code` with `Qt Extension Pack`
  - and so on

* To use `Xcode`, you have to configure project first by using `cmake`. The easiest way to do it is to use `Qt Creator` for configuration. Then open `AmneziaVPN.xcodeproj` file from the build folder by using `Xcode`. Note that none of the files changed are saved - the files actually getting changed in build directory. Copy them manually if necessary

* `Android studio` could be used in the same way - just configure the project by using `cmake` manually or by using `Qt Creator`. Open `<build-dir>/client/android-build` in `Android studio` then. Do not forget to copy the changes - everything you do is saved under the build directory actually.

### Installing Android SDK

* Android SDK could be installed using the following methods:
  - Using `Qt Creator`. Use `Preferences`->`SDKs`
  - Using `Android studio`. By default it installs necessary `SDKs` automatically during the installation
  - Manually by using `sdk-manager`. Check [this](https://developer.android.com/tools) page for details

## License

This project is licensed under the GNU General Public License v3.0 (see LICENSE) and also includes third-party components distributed under their own terms (see THIRD_PARTY_LICENSES.md).

## Donate

Patreon: [https://www.patreon.com/amneziavpn](https://www.patreon.com/amneziavpn)

Bitcoin: bc1qmhtgcf9637rl3kqyy22r2a8wa8laka4t9rx2mf <br>
USDT BEP20: 0x6abD576765a826f87D1D95183438f9408C901bE4 <br>
USDT TRC20: TELAitazF1MZGmiNjTcnxDjEiH5oe7LC9d <br>
XMR: 48spms39jt1L2L5vyw2RQW6CXD6odUd4jFu19GZcDyKKQV9U88wsJVjSbL4CfRys37jVMdoaWVPSvezCQPhHXUW5UKLqUp3 <br> 
TON: UQDpU1CyKRmg7L8mNScKk9FRc2SlESuI7N-Hby4nX-CcVmns
## Acknowledgments

This project is tested with BrowserStack.
We express our gratitude to [BrowserStack](https://www.browserstack.com) for supporting our project.
