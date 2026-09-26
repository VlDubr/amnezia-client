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

// Amnezia Panel: one user — limit, access period, blocking, invite key, configs and traffic.
PageType {
    id: root

    readonly property var user: PanelController.user
    property bool issuing: false

    function formatBytes(bytes) {
        var units = ["B", "KB", "MB", "GB", "TB"]
        var value = bytes || 0
        var unit = 0
        while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++ }
        return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit]
    }

    function configStatus(config) {
        if (config.status === "blocked") return config.blocked_by === "user" ? qsTr("Blocked by user") : qsTr("Blocked")
        if (config.status === "inactive") return qsTr("Inactive")
        return qsTr("Active")
    }

    Component.onCompleted: PanelController.loadServers()

    Connections {
        target: PanelController

        function onUserDeleted() { PageController.closePage() }

        function onErrorTextChanged() {
            if (PanelController.errorText !== "") {
                PageController.showErrorMessage(PanelController.errorText)
            }
        }

        function onShareChanged() {
            root.issuing = false
            var share = PanelController.share
            if (!share.available) {
                PageController.showNotificationMessage(qsTr("This imported config cannot be issued again"))
                return
            }
            var text = share.vpn_key !== "" ? share.vpn_key : share.native
            showQuestionDrawer(share.name, text, qsTr("Copy"), qsTr("Close"),
                               function() { GC.copyToClipBoard(text) }, function() {})
        }

        function onInviteKeyChanged() {
            if (PanelController.inviteKey !== "") {
                var key = PanelController.inviteKey
                showQuestionDrawer(qsTr("Invite key"), key + "\n\n" + qsTr("The key is shown only once."),
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
            spacing: 12
            visible: root.user.id !== undefined

            BaseHeaderType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                headerText: root.user.display_name || ""
                descriptionText: (root.user.registered ? qsTr("Registered: ") + root.user.login : qsTr("Not registered yet"))
                                 + "\n" + qsTr("Downloaded: ") + formatBytes(root.user.traffic_total ? root.user.traffic_total.rx : 0)
                                 + " · " + qsTr("Uploaded: ") + formatBytes(root.user.traffic_total ? root.user.traffic_total.tx : 0)
            }

            Repeater {
                model: root.user.traffic_by_server || []
                delegate: ParagraphTextType {
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    text: modelData.server_name + ": ↓ " + formatBytes(modelData.rx) + " · ↑ " + formatBytes(modelData.tx)
                }
            }

            TextFieldWithHeaderType {
                id: limitField
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                headerText: qsTr("Config limit")
                textField.text: root.user.max_configs !== undefined ? String(root.user.max_configs) : ""
                textField.validator: IntValidator { bottom: 0; top: 1000 }
            }

            TextFieldWithHeaderType {
                id: expiresField
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                headerText: qsTr("Last day of access (YYYY-MM-DD, empty for none)")
                textField.text: root.user.expires_on || ""
                textField.validator: RegularExpressionValidator { regularExpression: /^(\d{4}-\d{2}-\d{2})?$/ }
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                enabled: !PanelController.busy
                text: qsTr("Save")
                clickedFunc: function() {
                    PanelController.saveUser(root.user.id, root.user.display_name,
                                             parseInt(limitField.textField.text || "0"), expiresField.textField.text,
                                             root.user.note || "")
                }
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                defaultColor: AmneziaStyle.color.transparent
                textColor: AmneziaStyle.color.paleGray
                text: root.user.blocked_by === "admin" ? qsTr("Unblock user") : qsTr("Block user")
                clickedFunc: function() {
                    if (root.user.blocked_by === "admin") {
                        PanelController.unblockUser(root.user.id)
                        return
                    }
                    showQuestionDrawer(qsTr("Block %1?").arg(root.user.display_name),
                                       qsTr("All configs of the user stop working but are kept."),
                                       qsTr("Block"), qsTr("Cancel"),
                                       function() { PanelController.blockUser(root.user.id) }, function() {})
                }
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                visible: !root.user.registered
                defaultColor: AmneziaStyle.color.transparent
                textColor: AmneziaStyle.color.paleGray
                text: qsTr("Issue a new invite key")
                clickedFunc: function() {
                    showQuestionDrawer(qsTr("Issue a new invite key?"), qsTr("The old key will stop working."),
                                       qsTr("Continue"), qsTr("Cancel"),
                                       function() { PanelController.reissueInvite(root.user.id) }, function() {})
                }
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                defaultColor: AmneziaStyle.color.transparent
                textColor: AmneziaStyle.color.vibrantRed
                text: qsTr("Delete user")
                clickedFunc: function() {
                    showQuestionDrawer(qsTr("Delete %1 and all configs?").arg(root.user.display_name),
                                       qsTr("This cannot be undone."), qsTr("Delete"), qsTr("Cancel"),
                                       function() { PanelController.deleteUser(root.user.id) }, function() {})
                }
            }

            BaseHeaderType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                Layout.topMargin: 16
                headerText: qsTr("Configs")
            }

            BasicButtonType {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16
                text: root.issuing ? qsTr("Cancel") : qsTr("Issue a config")
                clickedFunc: function() { root.issuing = !root.issuing }
            }

            Repeater {
                model: root.issuing ? PanelController.servers : []
                delegate: ColumnLayout {
                    Layout.fillWidth: true
                    property var serverData: modelData
                    Repeater {
                        model: serverData.containers
                        delegate: LabelWithButtonType {
                            Layout.fillWidth: true
                            text: serverData.name + " · " + modelData.title
                            rightImageSource: "qrc:/images/controls/plus.svg"
                            clickedFunction: function() {
                                PanelController.issueConfig(root.user.id, serverData.id, modelData.container)
                            }
                        }
                    }
                }
            }
        }

        model: root.user.configs || []

        delegate: ColumnLayout {
            width: listView.width

            LabelWithButtonType {
                Layout.fillWidth: true
                text: modelData.name
                descriptionText: modelData.server_name + " · " + modelData.protocol + " · " + configStatus(modelData)
                                 + " · ↓ " + formatBytes(modelData.traffic.rx)
                rightImageSource: "qrc:/images/controls/chevron-right.svg"

                clickedFunction: function() {
                    var config = modelData
                    var blocked = config.blocked_by !== null && config.blocked_by !== undefined
                    showQuestionDrawer(config.name, qsTr("Choose an action"),
                                       qsTr("Show"), blocked ? qsTr("Unblock") : qsTr("Block"),
                                       function() { PanelController.showConfig(config.id) },
                                       function() {
                                           if (blocked) PanelController.unblockConfig(config.id)
                                           else PanelController.blockConfig(config.id)
                                       })
                }
            }

            BasicButtonType {
                Layout.leftMargin: 16
                defaultColor: AmneziaStyle.color.transparent
                textColor: AmneziaStyle.color.vibrantRed
                text: qsTr("Delete config")
                clickedFunc: function() {
                    var config = modelData
                    showQuestionDrawer(qsTr("Delete %1?").arg(config.name), qsTr("The config stops working."),
                                       qsTr("Delete"), qsTr("Cancel"),
                                       function() { PanelController.deleteConfig(config.id) }, function() {})
                }
            }

            DividerType {}
        }
    }
}
