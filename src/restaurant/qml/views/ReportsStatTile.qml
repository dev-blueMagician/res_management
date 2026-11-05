import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// One labelled figure, on the home and report screens. Set clickable to make it a link.
// tone: "neutral", "warning" or "danger" colours the figure.
AbstractButton {
    id: root

    property string label: ""
    property string value: ""
    property string note: ""
    property string tone: "neutral"
    property bool clickable: false

    enabled: clickable
    hoverEnabled: clickable
    focusPolicy: clickable ? Qt.StrongFocus : Qt.NoFocus
    implicitWidth: 200
    implicitHeight: contentColumn.implicitHeight + 2 * padding
    padding: Theme.spacingM
    Accessible.name: label + ": " + value

    contentItem: ColumnLayout {
        id: contentColumn
        spacing: Theme.spacingXs

        Label {
            Layout.fillWidth: true
            text: root.label
            color: Theme.textMuted
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            font.weight: Font.DemiBold
        }

        Label {
            Layout.fillWidth: true
            text: root.value
            color: root.tone === "danger" ? Theme.danger
                   : root.tone === "warning" ? Theme.warning : Theme.text
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontTitle
            font.weight: Font.DemiBold
        }

        Label {
            Layout.fillWidth: true
            text: root.note
            visible: root.note !== ""
            color: root.clickable ? Theme.accent : Theme.textMuted
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            font.weight: root.clickable ? Font.DemiBold : Font.Normal
        }

        Item {
            Layout.fillHeight: true
        }
    }

    background: Rectangle {
        radius: Theme.radius
        color: root.down ? Theme.neutralSoft : root.hovered ? Theme.surfaceHover : Theme.surface
        border.width: root.visualFocus ? 2 : 1
        border.color: root.visualFocus ? Theme.accent : Theme.border
    }
}
