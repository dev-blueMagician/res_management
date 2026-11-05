import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// A status message that takes no space while empty.
// kind: "error" (default), "success", "warning" or "info".
Rectangle {
    id: root

    property string text: ""
    property string kind: "error"

    function show(message, messageKind) {
        text = message
        kind = messageKind || "error"
    }

    function clear() {
        text = ""
    }

    // Show a bridge result: its error, or successText if it worked. Returns result.ok.
    function showResult(result, successText) {
        if (!result.ok)
            show(result.error, "error")
        else if (successText)
            show(successText, "success")
        else
            clear()
        return result.ok
    }

    readonly property color _tone: kind === "success" ? Theme.success
                                   : kind === "warning" ? Theme.warning
                                   : kind === "info" ? Theme.textMuted : Theme.danger

    visible: text !== ""
    implicitWidth: 320
    implicitHeight: visible ? message.implicitHeight + 2 * Theme.spacingM - Theme.spacingXs : 0
    radius: Theme.radius
    color: kind === "success" ? Theme.successSoft
           : kind === "warning" ? Theme.warningSoft
           : kind === "info" ? Theme.neutralSoft : Theme.dangerSoft

    Rectangle {
        width: Theme.spacingXs
        height: parent.height
        radius: Theme.radiusSmall
        color: root._tone
    }

    Label {
        id: message
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingM
        anchors.rightMargin: Theme.spacingM
        text: root.text
        wrapMode: Text.Wrap
        color: Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
        Accessible.role: Accessible.AlertMessage
    }
}
