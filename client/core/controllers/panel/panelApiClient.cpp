#include "panelApiClient.h"

#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QUrl>

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

void PanelApiClient::signIn(const QString &url, const QString &login, const QString &password, const Callback &done)
{
    m_appSettings->setPanelUrl(normalizedBaseUrl(url));
    m_appSettings->setPanelToken(QString());
    QJsonObject body { { "login", login }, { "password", password }, { "role", "admin" } };
    send("POST", "/api/auth/login", &body, [this, done](const Result &result) {
        if (result.ok()) {
            m_appSettings->setPanelToken(result.body.object().value("token").toString());
        }
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
    QNetworkRequest request(QUrl(baseUrl() + path));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    request.setRawHeader("Accept", "application/json");
    request.setTransferTimeout(requestTimeoutMs);
    const QString token = m_appSettings->panelToken();
    if (!token.isEmpty()) {
        request.setRawHeader("Authorization", "Bearer " + token.toUtf8());
    }
    const QByteArray payload = body ? QJsonDocument(*body).toJson(QJsonDocument::Compact) : QByteArray();
    QNetworkReply *reply = m_network->sendCustomRequest(request, method, payload);

    connect(reply, &QNetworkReply::finished, this, [this, reply, done]() {
        reply->deleteLater();
        Result result;
        result.status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const QByteArray data = reply->readAll();
        result.body = QJsonDocument::fromJson(data);

        if (result.status == 0) {
            result.errorCode = QStringLiteral("network");
            result.errorMessage = reply->errorString();
        } else if (result.status >= 400) {
            const QJsonObject error = result.body.object();
            result.errorCode = error.value("code").toString(QStringLiteral("http_%1").arg(result.status));
            result.errorMessage = error.value("message").toString(reply->errorString());
            const bool roleLost = result.status == 403 && result.errorCode == QLatin1String("forbidden");
            if (result.status == 401 || roleLost) {
                m_appSettings->setPanelToken(QString());
                emit sessionExpired();
            }
        }
        done(result);
    });
}
