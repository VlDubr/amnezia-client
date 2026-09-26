#include "panelUiController.h"

#include <QJsonArray>
#include <QUrlQuery>

namespace
{
    QString errorTextFor(const QString &code, const QString &message)
    {
        static const QHash<QString, const char *> texts {
            { "invalid_credentials", QT_TR_NOOP("Wrong login or password") },
            { "rate_limited", QT_TR_NOOP("Too many attempts. Wait a bit and try again.") },
            { "config_limit", QT_TR_NOOP("Config limit reached") },
            { "server_unavailable", QT_TR_NOOP("The server does not respond. Try again later.") },
            { "user_blocked", QT_TR_NOOP("Access is blocked") },
            { "user_expired", QT_TR_NOOP("Access period has ended") },
            { "already_registered", QT_TR_NOOP("The user has already registered") },
            { "not_found", QT_TR_NOOP("Not found") },
            { "forbidden", QT_TR_NOOP("Administrator sign-in required") },
            { "unauthorized", QT_TR_NOOP("Administrator sign-in required") },
            { "network", QT_TR_NOOP("No connection to the panel") },
        };
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
    connect(m_api, &PanelApiClient::sessionExpired, this, &PanelUiController::signedInChanged);
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

void PanelUiController::signIn(const QString &url, const QString &login, const QString &password)
{
    clearError();
    m_api->signIn(url, login, password, handle([this](const PanelApiClient::Result &) { emit signedInChanged(); }));
}

void PanelUiController::signOut()
{
    m_api->signOut();
    m_users.clear();
    m_user.clear();
    emit usersChanged();
    emit userChanged();
    emit signedInChanged();
}

void PanelUiController::loadUsers(const QString &query)
{
    m_lastQuery = query;
    QUrlQuery q;
    if (!query.trimmed().isEmpty()) {
        q.addQueryItem("q", query.trimmed());
    }
    m_api->get("/api/admin/users?" + q.toString(QUrl::FullyEncoded), handle([this](const PanelApiClient::Result &r) {
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
    m_api->get(QString("/api/admin/users/%1").arg(id), handle([this](const PanelApiClient::Result &r) {
                   m_user = r.body.object().toVariantMap();
                   emit userChanged();
               }));
}

void PanelUiController::reloadUser()
{
    if (m_user.contains("id")) {
        loadUser(m_user.value("id").toInt());
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
                   QVariantMap share = config.value("export").toObject().toVariantMap();
                   share["name"] = config.value("name").toString();
                   share["available"] = config.value("export").isObject();
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
