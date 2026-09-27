#ifndef PANELAPICLIENT_H
#define PANELAPICLIENT_H

#include <QJsonDocument>
#include <QJsonObject>
#include <QObject>
#include <QString>

#include <functional>

class QNetworkAccessManager;
class SecureAppSettingsRepository;

// REST client for an Amnezia Panel (panel/backend). Authenticates with the admin session token as a Bearer
// header; errors arrive as {code, message} and are passed on unchanged so the UI can translate the code.
class PanelApiClient : public QObject
{
    Q_OBJECT

public:
    struct Result
    {
        int status = 0;
        QJsonDocument body;
        QString errorCode; // empty on success
        QString errorMessage;
        // With errorCode "untrusted_certificate" or "certificate_changed": SHA-256 of the certificate presented
        QString certSha256;

        bool ok() const { return errorCode.isEmpty(); }
    };
    using Callback = std::function<void(const Result &)>;

    explicit PanelApiClient(SecureAppSettingsRepository *appSettings, QNetworkAccessManager *network,
                            QObject *parent = nullptr);

    QString baseUrl() const;
    bool hasToken() const;

    // trustedCertSha256: a self-signed certificate the admin has just confirmed; it is kept after a successful sign-in.
    void signIn(const QString &url, const QString &login, const QString &password, const QString &trustedCertSha256,
                const Callback &done);
    void signOut();

    void get(const QString &path, const Callback &done);
    void post(const QString &path, const QJsonObject &body, const Callback &done);
    void patch(const QString &path, const QJsonObject &body, const Callback &done);
    void remove(const QString &path, const Callback &done);

signals:
    // The token was rejected: the admin has to sign in again.
    void sessionExpired();

private:
    void send(const QByteArray &method, const QString &path, const QJsonObject *body, const Callback &done);
    void sendTo(const QString &base, const QString &token, const QString &pinnedCertSha256, const QByteArray &method,
                const QString &path, const QJsonObject *body, const Callback &done);

    SecureAppSettingsRepository *m_appSettings;
    QNetworkAccessManager *m_network;
};

#endif // PANELAPICLIENT_H
