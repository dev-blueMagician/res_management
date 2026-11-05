import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// The heading of a screen. Children, such as action buttons, sit on the right.
RowLayout {
    id: root

    property string title: ""
    property string subtitle: ""
    default property alias actions: actionRow.data

    spacing: Theme.spacingM

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2

        Label {
            Layout.fillWidth: true
            text: root.title
            color: Theme.text
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontTitle
            font.weight: Font.DemiBold
        }

        Label {
            Layout.fillWidth: true
            text: root.subtitle
            visible: root.subtitle !== ""
            color: Theme.textMuted
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }
    }

    RowLayout {
        id: actionRow
        spacing: Theme.spacingS
    }
}
