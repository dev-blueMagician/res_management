import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// Stands in for a screen that has not been built yet.
ColumnLayout {
    id: root

    property string title: ""

    spacing: Theme.spacingL

    PageHeader {
        Layout.fillWidth: true
        title: root.title
    }

    Card {
        Layout.fillWidth: true
        Layout.fillHeight: true

        Label {
            Layout.alignment: Qt.AlignCenter
            text: "This screen is not available yet."
            color: Theme.textMuted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }
    }
}
