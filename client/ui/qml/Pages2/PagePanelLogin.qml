import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import PageEnum 1.0
import Style 1.0

import "./"
import "../Controls2"
import "../Config"
import "../Controls2/TextTypes"
import "../Components"

// Amnezia Panel: administrator sign-in (panel address, login, password).
PageType {
    id: root

    // What the last sign-in used, to repeat it once the admin trusts the panel's certificate.
    property string pendingUrl
    property string pendingLogin
    property string pendingPassword

    Connections {
        target: PanelController

        function onSignedInChanged() {
            if (PanelController.isSignedIn && root.StackView.status === StackView.Active) {
                // Replace the sign-in page: Back from the user list must not return to it.
                PageController.closePage()
                PageController.goToPage(PageEnum.PagePanelUsers)
            }
        }

        function onCertificateUntrusted(sha256, changed) {
            if (root.StackView.status !== StackView.Active) {
                return
            }
            var text = changed
                    ? qsTr("The panel's certificate has changed since the last sign-in. If you did not replace it on "
                           + "the server, do not continue: someone may be intercepting the connection.")
                    : qsTr("The panel uses a self-signed certificate. Continue only if its SHA-256 fingerprint "
                           + "matches the one gen-self-signed-cert.sh printed on the server.")
            showQuestionDrawer(qsTr("Check the panel's certificate"), text + "

" + sha256,
                               qsTr("Trust and sign in"), qsTr("Cancel"),
                               function() {
                                   PanelController.signIn(root.pendingUrl, root.pendingLogin, root.pendingPassword,
                                                          sha256)
                               },
                               function() { root.pendingPassword = "" })
        }

        function onErrorTextChanged() {
            if (PanelController.errorText !== "" && root.StackView.status === StackView.Active) {
                PageController.showErrorMessage(PanelController.errorText)
            }
        }
    }

    BackButtonType {
        id: backButton

        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: 20 + PageController.safeAreaTopMargin
    }

    ListViewType {
        id: listView

        anchors.top: backButton.bottom
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.left: parent.left

        model: 1

        delegate: ColumnLayout {
            width: listView.width
            spacing: 16

            BaseHeaderType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: qsTr("Amnezia Panel")
                descriptionText: qsTr("Manage panel users and their configs. Sign in as a panel administrator.")
            }

            TextFieldWithHeaderType {
                id: urlField

                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: qsTr("Panel address")
                textField.text: PanelController.panelUrl
                textField.placeholderText: "https://panel.example.com"
            }

            TextFieldWithHeaderType {
                id: loginField

                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: qsTr("Login")
            }

            TextFieldWithHeaderType {
                id: passwordField

                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: qsTr("Password")
                textField.echoMode: TextInput.Password
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                enabled: !PanelController.busy && urlField.textField.text !== "" && loginField.textField.text !== ""
                text: qsTr("Sign in")

                clickedFunc: function() {
                    root.pendingUrl = urlField.textField.text
                    root.pendingLogin = loginField.textField.text
                    root.pendingPassword = passwordField.textField.text
                    PanelController.signIn(root.pendingUrl, root.pendingLogin, root.pendingPassword)
                }
            }
        }
    }
}
