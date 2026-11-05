import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// A modal dialog with a title, a form body and confirm/cancel buttons. Children go
// in the form body, which is a ColumnLayout: give them Layout.fillWidth. It stays
// open after confirming, so a handler of submitted() can set errorText or close().
Dialog {
    id: root

    property string confirmText: "Save"
    property string cancelText: "Cancel"
    property bool danger: false
    property bool confirmEnabled: true
    property string errorText: ""
    default property alias formContent: body.data

    signal submitted()

    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    width: Math.min(Theme.dialogWidth, parent ? parent.width - 2 * Theme.spacingL : Theme.dialogWidth)
    modal: true
    closePolicy: Popup.CloseOnEscape
    padding: Theme.spacingL
    topPadding: Theme.spacingM
    onAboutToShow: errorText = ""

    Overlay.modal: Rectangle { color: Theme.scrim }

    background: Rectangle {
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
    }

    header: Label {
        text: root.title
        padding: Theme.spacingL
        bottomPadding: 0
        color: Theme.text
        wrapMode: Text.Wrap
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontLarge
        font.weight: Font.DemiBold
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingM

        MessageBanner {
            Layout.fillWidth: true
            text: root.errorText
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: Theme.spacingM
        }
    }

    footer: Item {
        implicitHeight: buttons.implicitHeight + Theme.spacingL

        RowLayout {
            id: buttons
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacingL
            spacing: Theme.spacingS

            AppButton {
                text: root.cancelText
                kind: "ghost"
                onClicked: root.reject()
            }

            AppButton {
                text: root.confirmText
                kind: root.danger ? "danger" : "primary"
                enabled: root.confirmEnabled
                onClicked: root.submitted()
            }
        }
    }
}
