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

// Amnezia Panel: user list, search and user creation with a one-time invite key.
PageType {
    id: root

    property bool creating: false

    function statusText(user) {
        if (user.status === "blocked") return qsTr("Blocked")
        if (user.status === "expired") return qsTr("Expired")
        if (user.status === "deleting") return qsTr("Being removed")
        return qsTr("Active")
    }

    Component.onCompleted: PanelController.loadUsers("")

    Connections {
        target: PanelController

        function onSignedInChanged() {
            if (!PanelController.isSignedIn) {
                PageController.goToPage(PageEnum.PagePanelLogin)
            }
        }

        function onErrorTextChanged() {
            if (PanelController.errorText !== "") {
                PageController.showErrorMessage(PanelController.errorText)
            }
        }

        function onInviteKeyChanged() {
            if (PanelController.inviteKey !== "") {
                root.creating = false
                var key = PanelController.inviteKey
                showQuestionDrawer(qsTr("Invite key"),
                                   key + "\n\n" + qsTr("The key is shown only once. Copy it and send it to the user."),
                                   qsTr("Copy"), qsTr("Close"),
                                   function() { GC.copyToClipBoard(key); PanelController.clearInviteKey() },
                                   function() { PanelController.clearInviteKey() })
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

        header: ColumnLayout {
            width: listView.width
            spacing: 16

            BaseHeaderType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: qsTr("Panel users")
                descriptionText: PanelController.panelUrl
            }

            TextFieldWithHeaderType {
                id: search

                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: qsTr("Search")
                textField.onEditingFinished: PanelController.loadUsers(search.textField.text)
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                visible: !root.creating
                text: qsTr("New user")
                clickedFunc: function() { root.creating = true }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                spacing: 12
                visible: root.creating

                TextFieldWithHeaderType {
                    id: newName
                    Layout.fillWidth: true
                    headerText: qsTr("Name")
                }

                TextFieldWithHeaderType {
                    id: newLimit
                    Layout.fillWidth: true
                    headerText: qsTr("Config limit")
                    textField.text: "3"
                    textField.validator: IntValidator { bottom: 0; top: 1000 }
                }

                TextFieldWithHeaderType {
                    id: newExpires
                    Layout.fillWidth: true
                    headerText: qsTr("Last day of access (YYYY-MM-DD, empty for none)")
                    textField.validator: RegularExpressionValidator { regularExpression: /^(\d{4}-\d{2}-\d{2})?$/ }
                }

                BasicButtonType {
                    Layout.fillWidth: true
                    enabled: !PanelController.busy && newName.textField.text !== ""
                    text: qsTr("Create")
                    clickedFunc: function() {
                        PanelController.createUser(newName.textField.text, parseInt(newLimit.textField.text || "0"),
                                                   newExpires.textField.text, "")
                    }
                }
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                defaultColor: AmneziaStyle.color.transparent

                textColor: AmneziaStyle.color.paleGray
                text: qsTr("Sign out of the panel")
                clickedFunc: function() { PanelController.signOut() }
            }
        }

        model: PanelController.users

        delegate: ColumnLayout {
            width: listView.width

            LabelWithButtonType {
                Layout.fillWidth: true

                text: modelData.display_name
                descriptionText: statusText(modelData) + " · " + modelData.configs_count + "/" + modelData.max_configs
                                 + (modelData.login ? " · " + modelData.login : "")
                rightImageSource: "qrc:/images/controls/chevron-right.svg"

                clickedFunction: function() {
                    PanelController.loadUser(modelData.id)
                    PageController.goToPage(PageEnum.PagePanelUser)
                }
            }

            DividerType {}
        }
    }
}
