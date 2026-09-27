#include "panelApiClient.h"

#include <QCryptographicHash>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSslCertificate>
#include <QSslConfiguration>
#include <QSslError>
#include <QUrl>

#include <memory>

#include "core/repositories/secureAppSettingsRepository.h"

namespace
{
    constexpr int requestTimeoutMs = 45000;

    QString normalizedBaseUrl(QString url)
    {
        url = url.trimmed();
        if (!url.startsWith(QLatin1String("http://")) && !url.startsWith(QLatin1String("https://"))) {
            url.prepend(QLatin1String("https://"));
        }
        while (url.endsWith(QLatin1Char('/'))) {
            url.chop(1);
        }
        return url;
    }

    // Plain http is accepted only for a panel on this machine: elsewhere it would send the password and the
    // admin token in clear text.
    bool isInsecureRemote(const QString &url)
    {
        const QUrl parsed(url);
        if (parsed.scheme() != QLatin1String("http")) {
            return false;
        }
        const QString host = parsed.host();
        return host != QLatin1String("localhost") && host != QLatin1String("127.0.0.1") && host != QLatin1String("::1");
    }

    // Same form as `openssl x509 -fingerprint -sha256` prints, so the admin can compare them by eye.
    QString sha256Of(const QSslCertificate &certificate)
    {
        return QString::fromLatin1(certificate.digest(QCryptographicHash::Sha256).toHex(':').toUpper());
    }
} // namespace

PanelApiClient::PanelApiClient(SecureAppSettingsRepository *appSettings, QNetworkAccessManager *network,
                               QObject *parent)
    : QObject(parent), m_appSettings(appSettings), m_network(network)
{
}

QString PanelApiClient::baseUrl() const
{
    return m_appSettings->panelUrl();
}

bool PanelApiClient::hasToken() const
{
    return !m_appSettings->panelToken().isEmpty();
}

void PanelApiClient::signIn(const QString &url, const QString &login, const QString &password,
                            const QString &trustedCertSha256, const Callback &done)
{
    const QString base = normalizedBaseUrl(url);
    if (isInsecureRemote(base)) {
        Result result;
        result.errorCode = QStringLiteral("insecure_url");
        done(result);
        return;
    }
    // One sign-in for every account: the server decides the role, and only an administrator may use these screens.
    QJsonObject body { { "login", login }, { "password", password } };
    // The address and the token are stored only after a successful sign-in, so a mistyped address does not
    // sign the admin out of a working panel.
    // A certificate trusted earlier stays trusted only for the same address.
    const QString pin = !trustedCertSha256.isEmpty() ? trustedCertSha256
            : base == baseUrl() ? m_appSettings->panelCertSha256()
                                : QString();
    sendTo(base, QString(), pin, "POST", "/api/auth/login", &body, [this, base, pin, done](const Result &result) {
        if (!result.ok()) {
            done(result);
            return;
        }
        const QJsonObject session = result.body.object();
        const QString token = session.value("token").toString();
        if (session.value("role").toString() != QLatin1String("admin")) {
            // A user account: close the session it just opened and refuse.
            sendTo(base, token, pin, "POST", "/api/auth/logout", nullptr, [](const Result &) {});
            Result refused;
            refused.status = 403;
            refused.errorCode = QStringLiteral("forbidden");
            done(refused);
            return;
        }
        m_appSettings->setPanelUrl(base);
        m_appSettings->setPanelToken(token);
        m_appSettings->setPanelCertSha256(pin);
        done(result);
    });
}

void PanelApiClient::signOut()
{
    if (hasToken()) {
        send("POST", "/api/auth/logout", nullptr, [](const Result &) {});
    }
    m_appSettings->setPanelToken(QString());
}

void PanelApiClient::get(const QString &path, const Callback &done)
{
    send("GET", path, nullptr, done);
}

void PanelApiClient::post(const QString &path, const QJsonObject &body, const Callback &done)
{
    send("POST", path, &body, done);
}

void PanelApiClient::patch(const QString &path, const QJsonObject &body, const Callback &done)
{
    send("PATCH", path, &body, done);
}

void PanelApiClient::remove(const QString &path, const Callback &done)
{
    send("DELETE", path, nullptr, done);
}

void PanelApiClient::send(const QByteArray &method, const QString &path, const QJsonObject *body, const Callback &done)
{
    sendTo(baseUrl(), m_appSettings->panelToken(), m_appSettings->panelCertSha256(), method, path, body, done);
}

void PanelApiClient::sendTo(const QString &base, const QString &token, const QString &pinnedCertSha256,
                            const QByteArray &method, const QString &path, const QJsonObject *body,
                            const Callback &done)
{
    QNetworkRequest request(QUrl(base + path));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    request.setRawHeader("Accept", "application/json");
    request.setTransferTimeout(requestTimeoutMs);
    if (!token.isEmpty()) {
        request.setRawHeader("Authorization", "Bearer " + token.toUtf8());
    }
    const QByteArray payload = body ? QJsonDocument(*body).toJson(QJsonDocument::Compact) : QByteArray();
    QNetworkReply *reply = m_network->sendCustomRequest(request, method, payload);

    // A certificate that fails the system checks is accepted only if it is exactly the one the admin trusted.
    auto rejectedCert = std::make_shared<QString>();
    const auto onSslErrors = [reply, pinnedCertSha256, rejectedCert](const QList<QSslError> &errors) {
        const QString presented = sha256Of(reply->sslConfiguration().peerCertificate());
        if (!pinnedCertSha256.isEmpty() && presented == pinnedCertSha256) {
            reply->ignoreSslErrors(errors);
            return;
        }
        *rejectedCert = presented;
    };
    connect(reply, &QNetworkReply::sslErrors, this, onSslErrors);

    const bool withToken = !token.isEmpty();
    connect(reply, &QNetworkReply::finished, this, [this, reply, done, withToken, pinnedCertSha256, rejectedCert]() {
        reply->deleteLater();
        Result result;
        result.status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const QByteArray data = reply->readAll();
        result.body = QJsonDocument::fromJson(data);

        if (result.status == 0 && !rejectedCert->isEmpty()) {
            // Not trusted by the system: either a self-signed certificate seen for the first time, or a different
            // certificate than the trusted one, which may mean the connection is being intercepted.
            result.errorCode = pinnedCertSha256.isEmpty() ? QStringLiteral("untrusted_certificate")
                                                          : QStringLiteral("certificate_changed");
            result.errorMessage = reply->errorString();
            result.certSha256 = *rejectedCert;
        } else if (result.status == 0) {
            result.errorCode = QStringLiteral("network");
            result.errorMessage = reply->errorString();
        } else if (result.status >= 400) {
            const QJsonObject error = result.body.object();
            result.errorCode = error.value("code").toString(QStringLiteral("http_%1").arg(result.status));
            result.errorMessage = error.value("message").toString(reply->errorString());
            const bool roleLost = result.status == 403 && result.errorCode == QLatin1String("forbidden");
            if (withToken && (result.status == 401 || roleLost)) {
                m_appSettings->setPanelToken(QString());
                emit sessionExpired();
            }
        }
        done(result);
    });
}
