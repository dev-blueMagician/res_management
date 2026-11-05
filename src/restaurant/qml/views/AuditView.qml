import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-S7: the audit log, one page at a time, newest first.
ColumnLayout {
    id: root

    objectName: "auditView"

    readonly property int pageSize: 50
    readonly property int _whenWidth: 136
    readonly property int _whoWidth: 110
    readonly property int _actionWidth: 220
    readonly property var _areaLabels: ({
        auth: "Account",
        backup: "Backup",
        inventory: "Inventory",
        menu: "Menu",
        settings: "Settings",
        staff: "Staff",
        stock: "Stock"
    })

    property var entries: []
    property int offset: 0
    readonly property bool hasNewer: offset > 0
    property bool hasOlder: false
    property string loadError: ""

    component HeaderText: Label {
        color: Theme.textMuted
        elide: Text.ElideRight
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }

    // "auth.sign_in_failed" reads as "Account: sign in failed", "sale_voided" as "Sale voided".
    function actionText(action) {
        var dot = action.indexOf(".")
        var what = action.substring(dot + 1).replace(/_/g, " ")
        if (dot < 0)
            return what.charAt(0).toUpperCase() + what.substring(1)
        var area = action.substring(0, dot)
        return (_areaLabels[area] || area) + ": " + what
    }

    function load(newOffset) {
        // One more than a page, to know whether older entries exist.
        var result = api.audit.listEntries(pageSize + 1, newOffset)
        if (!result.ok) {
            loadError = result.error
            banner.show(result.error, "error")
            entries = []
            hasOlder = false
            return
        }
        loadError = ""
        banner.clear()
        offset = newOffset
        hasOlder = result.data.length > pageSize
        entries = result.data.slice(0, pageSize)
        list.positionViewAtBeginning()
    }

    Component.onCompleted: load(0)
    spacing: Theme.spacingM

    PageHeader {
        Layout.fillWidth: true
        title: "Audit log"
        subtitle: "Sign-ins, staff changes, price changes, stock adjustments and voids, newest first."

        AppButton {
            text: "Refresh"
            kind: "secondary"
            onClicked: root.load(0)
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
                        Layout.preferredWidth: root._whenWidth
                        text: "When"
                    }

                    HeaderText {
                        Layout.preferredWidth: root._whoWidth
                        text: "Who"
                    }

                    HeaderText {
                        Layout.preferredWidth: root._actionWidth
                        text: "Action"
                    }

                    HeaderText {
                        Layout.fillWidth: true
                        text: "Details"
                    }
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
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
                model: root.entries
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                delegate: DataRow {
                    required property var modelData
                    width: ListView.view.width
                    implicitHeight: Math.max(Theme.rowHeight, details.implicitHeight + Theme.spacingM)

                    Label {
                        Layout.preferredWidth: root._whenWidth
                        text: modelData.created_at_text
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }

                    Label {
                        Layout.preferredWidth: root._whoWidth
                        text: modelData.username !== "" ? modelData.username : "Unknown"
                        color: modelData.username !== "" ? Theme.text : Theme.textMuted
                        elide: Text.ElideRight
                    }

                    Label {
                        Layout.preferredWidth: root._actionWidth
                        text: root.actionText(modelData.action)
                        color: Theme.text
                        wrapMode: Text.Wrap
                    }

                    Label {
                        id: details
                        Layout.fillWidth: true
                        text: modelData.details
                        color: Theme.textMuted
                        wrapMode: Text.Wrap
                    }
                }

                Label {
                    anchors.centerIn: parent
                    visible: list.count === 0
                    text: root.loadError !== "" ? "The audit log could not be loaded."
                                                : "Nothing has been logged yet."
                    color: Theme.textMuted
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Theme.border
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.margins: Theme.spacingS
                spacing: Theme.spacingS

                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.spacingS
                    text: root.entries.length === 0 ? ""
                          : "Showing entries " + (root.offset + 1) + " to "
                            + (root.offset + root.entries.length)
                    color: Theme.textMuted
                }

                AppButton {
                    text: "Newer"
                    kind: "secondary"
                    enabled: root.hasNewer
                    onClicked: root.load(Math.max(0, root.offset - root.pageSize))
                }

                AppButton {
                    text: "Older"
                    kind: "secondary"
                    enabled: root.hasOlder
                    onClicked: root.load(root.offset + root.pageSize)
                }
            }
        }
    }
}
