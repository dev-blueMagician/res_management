import QtQuick
import QtQuick.Controls
import "."

// A small label for a status. kind: "neutral" (default), "success", "warning" or "danger".
Rectangle {
    id: root

    property string text: ""
    property string kind: "neutral"

    implicitWidth: label.implicitWidth + 2 * Theme.spacingS + Theme.spacingXs
    implicitHeight: label.implicitHeight + Theme.spacingXs
    radius: Theme.radiusSmall
    color: kind === "success" ? Theme.successSoft
           : kind === "warning" ? Theme.warningSoft
           : kind === "danger" ? Theme.dangerSoft : Theme.neutralSoft

    Label {
        id: label
        anchors.centerIn: parent
        text: root.text
        color: root.kind === "success" ? Theme.success
               : root.kind === "warning" ? Theme.warning
               : root.kind === "danger" ? Theme.danger : Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }
}
