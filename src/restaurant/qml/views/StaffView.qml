import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-S3 to FR-S5: staff accounts. Staff are deactivated, never deleted.
ColumnLayout {
    id: root

    objectName: "staffView"

    readonly property bool narrow: width < 900 // the smallest windows drop the username column
    readonly property int _usernameWidth: 130
    readonly property int _roleWidth: 96
    readonly property int _statusWidth: 136
    readonly property int _actionsWidth: 344

    property var staff: []
    property var roles: []
    property string loadError: ""

    component HeaderText: Label {
        color: Theme.textMuted
        elide: Text.ElideRight
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }

    function reload() {
        var result = api.staff.listStaff()
        staff = result.ok ? result.data : []
        loadError = result.ok ? "" : result.error
        if (!result.ok)
            banner.show(result.error, "error")
    }

    function isSelf(record) {
        return record.id === api.session.user.id
    }

    function deactivate(record) {
        confirm.staffId = record.id
        confirm.staffName = record.full_name
        confirm.title = "Deactivate " + record.full_name + "?"
        confirm.message = isSelf(record)
                ? "This is your own account. You will be signed out and won't be able to sign in again until another manager reactivates you."
                : record.full_name + " will no longer be able to sign in. Their sales and history are kept."
        confirm.open()
    }

    function reactivate(record) {
        banner.showResult(api.staff.reactivateStaff(record.id),
                          record.full_name + " can sign in again.")
    }

    Component.onCompleted: {
        var result = api.staff.roles()
        roles = result.ok ? result.data : []
        reload()
    }
    spacing: Theme.spacingM

    Connections {
        target: api
        function onStaffChanged() { root.reload() }
    }

    PageHeader {
        Layout.fillWidth: true
        title: "Staff"
        subtitle: "Deactivated staff can't sign in. Their records are kept."

        AppButton {
            text: "New staff member"
            onClicked: formDialog.openNew(root.roles)
        }
    }

    MessageBanner {
        id: banner
        Layout.fillWidth: true
    }

    Card {
        Layout.fillWidth: true
        Layout.fillHeight: true
        padding: 1

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Theme.controlHeight
                color: Theme.surfaceAlt

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingM
                    anchors.rightMargin: Theme.spacingM
                    spacing: Theme.spacingM

                    HeaderText {
                        Layout.fillWidth: true
                        text: "Name"
                    }

                    HeaderText {
                        visible: !root.narrow
                        Layout.preferredWidth: root._usernameWidth
                        text: "Username"
                    }

                    HeaderText {
                        Layout.preferredWidth: root._roleWidth
                        text: "Role"
                    }

                    HeaderText {
                        Layout.preferredWidth: root._statusWidth
                        text: "Status"
                    }

                    Item { Layout.preferredWidth: root._actionsWidth }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                    color: Theme.border
                }
            }

            ListView {
                id: staffList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
                model: root.staff
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                delegate: DataRow {
                    required property var modelData
                    width: ListView.view.width
                    implicitHeight: 60

                    Label {
                        Layout.fillWidth: true
                        text: modelData.full_name
                        color: modelData.active ? Theme.text : Theme.textMuted
                        elide: Text.ElideRight
                        font.weight: Font.DemiBold
                    }

                    Label {
                        visible: !root.narrow
                        Layout.preferredWidth: root._usernameWidth
                        text: modelData.username
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }

                    Label {
                        Layout.preferredWidth: root._roleWidth
                        text: modelData.role_text
                        color: modelData.active ? Theme.text : Theme.textMuted
                        elide: Text.ElideRight
                    }

                    RowLayout {
                        Layout.fillWidth: false
                        Layout.preferredWidth: root._statusWidth
                        spacing: Theme.spacingXs

                        StatusChip {
                            text: modelData.active ? "Active" : "Deactivated"
                            kind: modelData.active ? "success" : "neutral"
                        }

                        StatusChip {
                            text: "You"
                            visible: root.isSelf(modelData)
                        }

                        Item { Layout.fillWidth: true }
                    }

                    RowLayout {
                        Layout.fillWidth: false
                        Layout.preferredWidth: root._actionsWidth
                        spacing: Theme.spacingS

                        AppButton {
                            text: "Edit"
                            kind: "secondary"
                            onClicked: formDialog.openEdit(modelData, root.roles)
                        }

                        AppButton {
                            text: "Reset password"
                            kind: "secondary"
                            onClicked: passwordDialog.openFor(modelData)
                        }

                        AppButton {
                            Layout.fillWidth: true
                            text: modelData.active ? "Deactivate" : "Reactivate"
                            kind: "ghost"
                            onClicked: modelData.active ? root.deactivate(modelData)
                                                        : root.reactivate(modelData)
                        }
                    }
                }

                Label {
                    anchors.centerIn: parent
                    width: parent.width - 2 * Theme.spacingXl
                    visible: staffList.count === 0
                    text: root.loadError !== "" ? "The staff list could not be loaded."
                                                : "No staff yet."
                    color: Theme.textMuted
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                }
            }
        }
    }

    ConfirmDialog {
        id: confirm
        property int staffId: 0
        property string staffName: ""
        confirmText: "Deactivate"
        danger: true
        onConfirmed: banner.showResult(api.staff.deactivateStaff(staffId),
                                       staffName + " was deactivated.")
    }

    StaffFormDialog {
        id: formDialog
        onSaved: (message) => banner.show(message, "success")
    }

    StaffPasswordDialog {
        id: passwordDialog
        onSaved: (message) => banner.show(message, "success")
    }
}
