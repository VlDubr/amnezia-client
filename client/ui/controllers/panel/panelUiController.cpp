#include "panelUiController.h"

#include <QJsonArray>
#include <QUrl>

namespace
{
    // QT_TRANSLATE_NOOP keeps the strings in the PanelUiController context that tr() looks them up in.
    QString errorTextFor(const QString &code, const QString &message)
    {
        static const QHash<QString, const char *> texts {
            { "invalid_credentials", QT_TRANSLATE_NOOP("PanelUiController", "Wrong login or password") },
            { "rate_limited", QT_TRANSLATE_NOOP("PanelUiController", "Too many attempts. Wait a bit and try again.") },
            { "config_limit", QT_TRANSLATE_NOOP("PanelUiController", "Config limit reached") },
            { "server_unavailable",
              QT_TRANSLATE_NOOP("PanelUiController", "The server does not respond. Try again later.") },
            { "server_full", QT_TRANSLATE_NOOP("PanelUiController", "The server has no room for another config") },
            { "unsupported_container",
              QT_TRANSLATE_NOOP("PanelUiController", "This protocol is not installed on the server") },
            { "validation_error", QT_TRANSLATE_NOOP("PanelUiController", "Check the entered values") },
            { "user_blocked", QT_TRANSLATE_NOOP("PanelUiController", "Access is blocked") },
            { "user_expired", QT_TRANSLATE_NOOP("PanelUiController", "Access period has ended") },
            { "already_registered", QT_TRANSLATE_NOOP("PanelUiController", "The user has already registered") },
            { "not_found", QT_TRANSLATE_NOOP("PanelUiController", "Not found") },
            { "forbidden", QT_TRANSLATE_NOOP("PanelUiController", "Administrator sign-in required") },
            { "unauthorized", QT_TRANSLATE_NOOP("PanelUiController", "Administrator sign-in required") },
            { "untrusted_certificate",
              QT_TRANSLATE_NOOP("PanelUiController",
                                "The panel's certificate is not trusted. Sign out and sign in again to check it.") },
            { "certificate_changed",
              QT_TRANSLATE_NOOP("PanelUiController",
                                "The panel's certificate has changed. If you did not replace it on the server, "
                                "someone may be intercepting the connection. Sign out and sign in again to trust "
                                "the new one.") },
            { "insecure_url",
              QT_TRANSLATE_NOOP("PanelUiController",
                                "Use an https:// address: http:// sends the password unencrypted") },
        };
        if (code == QLatin1String("network")) {
            // Keep the transport error: a TLS or certificate problem is otherwise impossible to tell apart.
            return PanelUiController::tr("No connection to the panel: %1").arg(message);
        }
        const auto it = texts.constFind(code);
        return it != texts.constEnd() ? PanelUiController::tr(it.value()) : message;
    }

    QJsonObject userBody(const QString &name, int maxConfigs, const QString &expiresOn, const QString &note)
    {
        QJsonObject body { { "display_name", name }, { "max_configs", maxConfigs }, { "note", note } };
        body["expires_on"] = expiresOn.trimmed().isEmpty() ? QJsonValue() : QJsonValue(expiresOn.trimmed());
        return body;
    }
} // namespace

PanelUiController::PanelUiController(PanelApiClient *api, QObject *parent) : QObject(parent), m_api(api)
{
    connect(m_api, &PanelApiClient::sessionExpired, this, [this]() {
        clearData();
        emit signedInChanged();
    });
}

bool PanelUiController::isSignedIn() const
{
    return m_api->hasToken();
}

QString PanelUiController::panelUrl() const
{
    return m_api->baseUrl();
}

void PanelUiController::setBusy(bool busy)
{
    m_pending += busy ? 1 : -1;
    const bool now = m_pending > 0;
    if (now != m_busy) {
        m_busy = now;
        emit busyChanged();
    }
}

void PanelUiController::setError(const PanelApiClient::Result &result)
{
    m_errorText = errorTextFor(result.errorCode, result.errorMessage);
    emit errorTextChanged();
}

void PanelUiController::clearError()
{
    m_errorText.clear();
    emit errorTextChanged();
}

void PanelUiController::clearInviteKey()
{
    m_inviteKey.clear();
    emit inviteKeyChanged();
}

PanelApiClient::Callback PanelUiController::handle(const std::function<void(const PanelApiClient::Result &)> &onSuccess)
{
    setBusy(true);
    return [this, onSuccess](const PanelApiClient::Result &result) {
        setBusy(false);
        if (!result.ok()) {
            setError(result);
            return;
        }
        onSuccess(result);
    };
}

void PanelUiController::signIn(const QString &url, const QString &login, const QString &password,
                               const QString &trustedCertSha256)
{
    clearError();
    setBusy(true);
    m_api->signIn(url, login, password, trustedCertSha256, [this](const PanelApiClient::Result &result) {
        setBusy(false);
        const bool changed = result.errorCode == QLatin1String("certificate_changed");
        if (changed || result.errorCode == QLatin1String("untrusted_certificate")) {
            emit certificateUntrusted(result.certSha256, changed); // the page asks the admin to compare it
            return;
        }
        if (!result.ok()) {
            setError(result);
            return;
        }
        emit signedInChanged();
    });
}

void PanelUiController::signOut()
{
    m_api->signOut();
    clearData();
    emit signedInChanged();
}

void PanelUiController::clearData()
{
    m_users.clear();
    m_user.clear();
    m_share.clear();
    m_requestedUserId = 0;
    emit usersChanged();
    emit userChanged();
    emit shareChanged();
}

void PanelUiController::loadUsers(const QString &query)
{
    m_lastQuery = query;
    // Percent-encode every reserved character: the server would read a literal '+' as a space.
    const QString trimmed = query.trimmed();
    const QString q = trimmed.isEmpty() ? QString() : "q=" + QString::fromLatin1(QUrl::toPercentEncoding(trimmed));
    m_api->get("/api/admin/users?" + q, handle([this](const PanelApiClient::Result &r) {
                   m_users = r.body.array().toVariantList();
                   emit usersChanged();
               }));
}

void PanelUiController::createUser(const QString &name, int maxConfigs, const QString &expiresOn, const QString &note)
{
    clearError();
    m_api->post("/api/admin/users", userBody(name, maxConfigs, expiresOn, note),
                handle([this](const PanelApiClient::Result &r) {
                    m_inviteKey = r.body.object().value("invite_key").toString();
                    emit inviteKeyChanged();
                    loadUsers(m_lastQuery);
                }));
}

void PanelUiController::loadUser(int id)
{
    // Never show (and act on) the previously opened user while the new one loads.
    if (m_user.value("id").toInt() != id) {
        m_user.clear();
        emit userChanged();
    }
    m_requestedUserId = id;
    m_api->get(QString("/api/admin/users/%1").arg(id), handle([this, id](const PanelApiClient::Result &r) {
                   if (id != m_requestedUserId) {
                       return; // another user was opened meanwhile
                   }
                   m_user = r.body.object().toVariantMap();
                   emit userChanged();
               }));
}

void PanelUiController::reloadUser()
{
    if (m_requestedUserId != 0) {
        loadUser(m_requestedUserId);
    }
}

void PanelUiController::saveUser(int id, const QString &name, int maxConfigs, const QString &expiresOn,
                                 const QString &note)
{
    clearError();
    m_api->patch(QString("/api/admin/users/%1").arg(id), userBody(name, maxConfigs, expiresOn, note),
                 handle([this](const PanelApiClient::Result &) { reloadUser(); }));
}

void PanelUiController::blockUser(int id)
{
    m_api->post(QString("/api/admin/users/%1/block").arg(id), {},
                handle([this](const PanelApiClient::Result &) { reloadUser(); }));
}

void PanelUiController::unblockUser(int id)
{
    m_api->post(QString("/api/admin/users/%1/unblock").arg(id), {},
                handle([this](const PanelApiClient::Result &) { reloadUser(); }));
}

void PanelUiController::reissueInvite(int id)
{
    m_api->post(QString("/api/admin/users/%1/invite").arg(id), {}, handle([this](const PanelApiClient::Result &r) {
                    m_inviteKey = r.body.object().value("invite_key").toString();
                    emit inviteKeyChanged();
                }));
}

void PanelUiController::deleteUser(int id)
{
    m_api->remove(QString("/api/admin/users/%1").arg(id), handle([this](const PanelApiClient::Result &) {
                      m_user.clear();
                      emit userChanged();
                      emit userDeleted();
                      loadUsers(m_lastQuery);
                  }));
}

void PanelUiController::loadServers()
{
    m_api->get("/api/admin/servers", handle([this](const PanelApiClient::Result &r) {
                   m_servers = r.body.array().toVariantList();
                   emit serversChanged();
               }));
}

void PanelUiController::issueConfig(int userId, int serverId, const QString &container)
{
    clearError();
    QJsonObject body { { "server_id", serverId }, { "container", container } };
    m_api->post(QString("/api/admin/users/%1/configs").arg(userId), body,
                handle([this](const PanelApiClient::Result &r) {
                    reloadUser();
                    showConfig(r.body.object().value("id").toInt());
                }));
}

void PanelUiController::showConfig(int configId)
{
    m_api->get(QString("/api/admin/configs/%1").arg(configId), handle([this](const PanelApiClient::Result &r) {
                   const QJsonObject config = r.body.object();
                   const QJsonObject exported = config.value("export").toObject();
                   QVariantMap share = exported.toVariantMap();
                   share["name"] = config.value("name").toString();
                   share["available"] = config.value("export").isObject();
                   const QByteArray svg = exported.value("qr_svg").toString().toUtf8();
                   share["qr"] = svg.isEmpty() ? QString()
                                               : "data:image/svg;base64," + QString::fromLatin1(svg.toBase64());
                   m_share = share;
                   emit shareChanged();
               }));
}

void PanelUiController::blockConfig(int configId)
{
    m_api->post(QString("/api/admin/configs/%1/block").arg(configId), {},
                handle([this](const PanelApiClient::Result &) { reloadUser(); }));
}

void PanelUiController::unblockConfig(int configId)
{
    m_api->post(QString("/api/admin/configs/%1/unblock").arg(configId), {},
                handle([this](const PanelApiClient::Result &) { reloadUser(); }));
}

void PanelUiController::deleteConfig(int configId)
{
    m_api->remove(QString("/api/admin/configs/%1").arg(configId),
                  handle([this](const PanelApiClient::Result &) { reloadUser(); }));
}
