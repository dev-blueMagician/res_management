import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// A labelled text field. Give it Layout.fillWidth inside a layout, or a width.
ColumnLayout {
    id: root

    property string label: ""
    property string errorText: ""
    property bool large: false
    property alias text: field.text
    property alias placeholderText: field.placeholderText
    property alias echoMode: field.echoMode
    property alias readOnly: field.readOnly
    property alias maximumLength: field.maximumLength
    property alias inputMethodHints: field.inputMethodHints

    signal accepted()

    function focusInput() {
        field.forceActiveFocus()
    }

    spacing: Theme.spacingXs

    Label {
        text: root.label
        visible: root.label !== ""
        color: Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
        Accessible.ignored: true
    }

    TextField {
        id: field
        Layout.fillWidth: true
        implicitWidth: 240
        implicitHeight: root.large ? Theme.touchHeight : Theme.controlHeight
        leftPadding: Theme.spacingM - Theme.spacingXs
        rightPadding: Theme.spacingM - Theme.spacingXs
        selectByMouse: true
        color: Theme.text
        placeholderTextColor: Theme.textDisabled
        selectionColor: Theme.accent
        selectedTextColor: Theme.textOnAccent
        font.family: Theme.fontFamily
        font.pixelSize: root.large ? Theme.fontLarge : Theme.fontBody
        Accessible.name: root.label
        onAccepted: root.accepted()

        background: Rectangle {
            radius: Theme.radius
            color: field.readOnly ? Theme.surfaceAlt : Theme.surface
            border.width: field.activeFocus ? 2 : 1
            border.color: root.errorText !== "" ? Theme.danger
                          : field.activeFocus ? Theme.accent : Theme.borderStrong
        }
    }

    Label {
        Layout.fillWidth: true
        text: root.errorText
        visible: root.errorText !== ""
        color: Theme.danger
        wrapMode: Text.Wrap
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
    }
}
