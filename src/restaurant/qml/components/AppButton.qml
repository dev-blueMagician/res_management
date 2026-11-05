import QtQuick
import QtQuick.Controls
import "."

// kind: "primary" (default), "secondary", "danger" or "ghost".
// large: true gives the touch-sized version for the sales screen.
Button {
    id: control

    property string kind: "primary"
    property bool large: false

    readonly property bool _filled: kind === "primary" || kind === "danger"
    readonly property color _fill: {
        if (kind === "danger")
            return control.down ? Theme.dangerPressed : control.hovered ? Theme.dangerHover : Theme.danger
        if (kind === "primary")
            return control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent
        if (control.down)
            return Theme.border
        if (control.hovered)
            return Theme.surfaceHover
        return kind === "ghost" ? "transparent" : Theme.surface
    }

    implicitHeight: large ? Theme.touchHeight : Theme.controlHeight
    leftPadding: large ? Theme.spacingL : Theme.spacingM + Theme.spacingXs
    rightPadding: leftPadding
    topPadding: 0
    bottomPadding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.family: Theme.fontFamily
    font.pixelSize: large ? Theme.fontLarge : Theme.fontBody
    font.weight: Font.DemiBold
    opacity: enabled ? 1 : 0.45

    contentItem: Label {
        text: control.text
        font: control.font
        color: control._filled ? Theme.textOnAccent : Theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: Theme.radius
        color: control._fill
        border.width: control.visualFocus ? 2 : control.kind === "secondary" ? 1 : 0
        border.color: control.visualFocus
                      ? (control._filled ? Theme.text : Theme.accent)
                      : Theme.borderStrong
    }
}
