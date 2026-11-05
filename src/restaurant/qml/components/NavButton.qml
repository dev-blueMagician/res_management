import QtQuick
import QtQuick.Controls
import "."

// One entry of the left navigation.
AbstractButton {
    id: control

    property bool current: false

    implicitHeight: Theme.touchHeight - Theme.spacingS - Theme.spacingXs
    implicitWidth: Theme.sidebarWidth
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody
    font.weight: current ? Font.DemiBold : Font.Normal
    Accessible.role: Accessible.PageTab

    contentItem: Label {
        leftPadding: Theme.spacingM
        text: control.text
        font: control.font
        color: control.current || control.hovered ? Theme.sidebarText : Theme.sidebarTextMuted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: Theme.radius
        color: control.current ? Theme.sidebarActive
               : control.down || control.hovered ? Theme.sidebarHover : "transparent"
        border.width: control.visualFocus ? 2 : 0
        border.color: Theme.sidebarTextMuted

        Rectangle {
            visible: control.current
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: Theme.spacingXs
            height: parent.height - 2 * Theme.spacingS
            radius: 2
            color: Theme.accent
        }
    }
}
