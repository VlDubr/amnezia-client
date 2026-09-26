#ifndef PANELUICONTROLLER_H
#define PANELUICONTROLLER_H

#include <QObject>
#include <QVariantList>
#include <QVariantMap>

#include "core/controllers/panel/panelApiClient.h"

// Amnezia Panel administration for QML: users (А.1, А.4-А.7) and their configs (А.2) through the panel API.
class PanelUiController : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool isSignedIn READ isSignedIn NOTIFY signedInChanged)
    Q_PROPERTY(QString panelUrl READ panelUrl NOTIFY signedInChanged)
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorTextChanged)
    Q_PROPERTY(QVariantList users READ users NOTIFY usersChanged)
    Q_PROPERTY(QVariantMap user READ user NOTIFY userChanged)
    Q_PROPERTY(QVariantList servers READ servers NOTIFY serversChanged)
    Q_PROPERTY(QString inviteKey READ inviteKey NOTIFY inviteKeyChanged)
    Q_PROPERTY(QVariantMap share READ share NOTIFY shareChanged)

public:
    explicit PanelUiController(PanelApiClient *api, QObject *parent = nullptr);

    bool isSignedIn() const;
    QString panelUrl() const;
    bool busy() const { return m_busy; }
    QString errorText() const { return m_errorText; }
    QVariantList users() const { return m_users; }
    QVariantMap user() const { return m_user; }
    QVariantList servers() const { return m_servers; }
    QString inviteKey() const { return m_inviteKey; }
    QVariantMap share() const { return m_share; }

public slots:
    void signIn(const QString &url, const QString &login, const QString &password);
    void signOut();
    void clearError();
    void clearInviteKey();

    void loadUsers(const QString &query);
    void createUser(const QString &name, int maxConfigs, const QString &expiresOn, const QString &note);
    void loadUser(int id);
    void saveUser(int id, const QString &name, int maxConfigs, const QString &expiresOn, const QString &note);
    void blockUser(int id);
    void unblockUser(int id);
    void reissueInvite(int id);
    void deleteUser(int id);

    void loadServers();
    void issueConfig(int userId, int serverId, const QString &container);
    void showConfig(int configId);
    void blockConfig(int configId);
    void unblockConfig(int configId);
    void deleteConfig(int configId);

signals:
    void signedInChanged();
    void busyChanged();
    void errorTextChanged();
    void usersChanged();
    void userChanged();
    void serversChanged();
    void inviteKeyChanged();
    void shareChanged();
    void userDeleted();

private:
    PanelApiClient::Callback handle(const std::function<void(const PanelApiClient::Result &)> &onSuccess);
    void setBusy(bool busy);
    void setError(const PanelApiClient::Result &result);
    void reloadUser();

    PanelApiClient *m_api;
    bool m_busy = false;
    int m_pending = 0;
    QString m_errorText;
    QVariantList m_users;
    QVariantMap m_user;
    QVariantList m_servers;
    QString m_inviteKey;
    QVariantMap m_share;
    QString m_lastQuery;
};

#endif // PANELUICONTROLLER_H
