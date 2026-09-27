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

    Connections {
        target: PanelController

        function onSignedInChanged() {
            if (PanelController.isSignedIn && root.StackView.status === StackView.Active) {
                // Replace the sign-in page: Back from the user list must not return to it.
                PageController.closePage()
                PageController.goToPage(PageEnum.PagePanelUsers)
            }
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
                    PanelController.signIn(urlField.textField.text, loginField.textField.text,
                                           passwordField.textField.text)
                }
            }
        }
    }
}
