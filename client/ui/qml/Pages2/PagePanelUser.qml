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
        if (config.status === "deleting") return qsTr("Being removed")
        if (config.status === "blocked") return config.blocked_by === "user" ? qsTr("Blocked by user") : qsTr("Blocked")
        if (config.status === "inactive") return qsTr("Inactive")
        return qsTr("Active")
    }

    function userStatus(user) {
        if (user.status === "blocked") return qsTr("Blocked")
        if (user.status === "expired") return qsTr("Expired")
        if (user.status === "deleting") return qsTr("Being removed")
        return qsTr("Active")
    }

    function loadText(level) {
        if (level === "low") return qsTr("Low load")
        if (level === "medium") return qsTr("Medium load")
        if (level === "high") return qsTr("High load")
        return qsTr("No load data")
    }

    function isActive() {
        return root.StackView.status === StackView.Active
    }

    Component.onCompleted: PanelController.loadServers()

    Connections {
        target: PanelController

        function onUserDeleted() { PageController.closePage() }

        function onErrorTextChanged() {
            if (PanelController.errorText !== "" && root.isActive()) {
                PageController.showErrorMessage(PanelController.errorText)
            }
        }

        function onShareChanged() {
            var share = PanelController.share
            if (share.name === undefined || !root.isActive()) {
                return // cleared on sign-out
            }
            if (!share.available) {
                PageController.showNotificationMessage(qsTr("This imported config cannot be issued again"))
                return
            }
            shareDrawer.openTriggered()
        }

        function onInviteKeyChanged() {
            if (PanelController.inviteKey !== "" && root.isActive()) {
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

                readonly property var traffic: root.user.traffic_total || { "rx": 0, "tx": 0 }

                headerText: root.user.display_name || ""
                descriptionText: userStatus(root.user) + " · "
                                 + (root.user.registered ? qsTr("Registered: ") + root.user.login
                                                         : qsTr("Not registered yet"))
                                 + "\n" + qsTr("Downloaded: ") + formatBytes(traffic.rx)
                                 + " · " + qsTr("Uploaded: ") + formatBytes(traffic.tx)
            }

            Repeater {
                model: root.user.traffic_by_server || []
                delegate: ParagraphTextType {
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    text: modelData.server_name + ": ↓ " + formatBytes(modelData.rx)
                          + " · ↑ " + formatBytes(modelData.tx)
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
                // An empty limit is not 0: Save waits until both fields hold complete values.
                enabled: !PanelController.busy && limitField.textField.acceptableInput
                         && expiresField.textField.acceptableInput
                text: qsTr("Save")
                clickedFunc: function() {
                    PanelController.saveUser(root.user.id, root.user.display_name,
                                             parseInt(limitField.textField.text), expiresField.textField.text,
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
                            // Creating a config takes a while over SSH: one tap issues exactly one config.
                            enabled: !PanelController.busy
                            text: serverData.name + " · " + modelData.title
                            descriptionText: loadText(serverData.load)
                            rightImageSource: "qrc:/images/controls/plus.svg"
                            clickedFunction: function() {
                                root.issuing = false
                                PanelController.issueConfig(root.user.id, serverData.id, modelData.container)
                            }
                        }
                    }
                }
            }
        }

        model: root.user.configs || []

        delegate: ColumnLayout {
            id: configRow

            width: listView.width

            // A config being removed keeps no actions: the server is still dropping it.
            readonly property bool removing: modelData.status === "deleting"
            readonly property bool blocked: modelData.blocked_by !== null && modelData.blocked_by !== undefined

            LabelWithButtonType {
                Layout.fillWidth: true
                enabled: !configRow.removing
                text: modelData.name
                descriptionText: modelData.server_name + " · " + modelData.protocol + " · " + configStatus(modelData)
                                 + " · ↓ " + formatBytes(modelData.traffic.rx)
                rightImageSource: configRow.removing ? "" : "qrc:/images/controls/chevron-right.svg"

                clickedFunction: function() { PanelController.showConfig(modelData.id) }
            }

            BasicButtonType {
                Layout.leftMargin: 16
                visible: !configRow.removing
                defaultColor: AmneziaStyle.color.transparent
                textColor: AmneziaStyle.color.paleGray
                text: configRow.blocked ? qsTr("Unblock config") : qsTr("Block config")
                clickedFunc: function() {
                    var config = modelData
                    if (configRow.blocked) {
                        PanelController.unblockConfig(config.id)
                        return
                    }
                    showQuestionDrawer(qsTr("Block %1?").arg(config.name),
                                       qsTr("The config stops working but is kept."), qsTr("Block"), qsTr("Cancel"),
                                       function() { PanelController.blockConfig(config.id) }, function() {})
                }
            }

            BasicButtonType {
                Layout.leftMargin: 16
                visible: !configRow.removing
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
    DrawerType2 {
        id: shareDrawer

        anchors.fill: parent
        expandedHeight: root.height * 0.9

        expandedStateContent: Item {
            id: shareView

            readonly property var share: PanelController.share
            readonly property string text: share.vpn_key ? share.vpn_key : (share.native || "")

            BackButtonType {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.topMargin: 16
                backButtonFunction: function() { shareDrawer.closeTriggered() }
            }

            FlickableType {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.topMargin: 56
                contentHeight: shareContent.height + 32

                ColumnLayout {
                    id: shareContent

                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    spacing: 16

                    Header2Type {
                        Layout.fillWidth: true
                        headerText: shareView.share.name || ""
                    }

                    Rectangle {
                        Layout.preferredWidth: Math.min(shareContent.width, root.height * 0.45, 360)
                        Layout.preferredHeight: Layout.preferredWidth
                        Layout.alignment: Qt.AlignHCenter
                        visible: !!shareView.share.qr
                        color: "white"
                        radius: 12

                        Image {
                            anchors.fill: parent
                            anchors.margins: 8
                            smooth: false
                            fillMode: Image.PreserveAspectFit
                            sourceSize.width: width
                            sourceSize.height: height
                            source: shareView.share.qr || ""
                        }
                    }

                    BasicButtonType {
                        Layout.fillWidth: true
                        text: qsTr("Copy")
                        clickedFunc: function() {
                            GC.copyToClipBoard(shareView.text)
                            PageController.showNotificationMessage(qsTr("Copied"))
                        }
                    }

                    TextArea {
                        Layout.fillWidth: true
                        readOnly: true
                        color: AmneziaStyle.color.paleGray
                        selectionColor: AmneziaStyle.color.richBrown
                        selectedTextColor: AmneziaStyle.color.paleGray
                        font.pixelSize: 14
                        font.family: "PT Root UI VF"
                        // A vpn:// key has no spaces: wrap anywhere so all of it is visible.
                        wrapMode: Text.WrapAnywhere
                        text: shareView.text
                        background: Rectangle { color: AmneziaStyle.color.transparent }
                    }
                }
            }
        }
    }
}
