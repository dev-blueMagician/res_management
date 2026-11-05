import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// One row of a list. Children go in a RowLayout. Set clickable to get a hover state
// and clicked(); buttons inside the row keep working on their own.
Rectangle {
    id: root

    property bool clickable: false
    property bool selected: false
    default property alias content: row.data

    signal clicked()

    implicitHeight: Theme.rowHeight
    implicitWidth: row.implicitWidth + 2 * Theme.spacingM
    color: selected ? Theme.accentSoft
           : clickable && area.containsMouse ? Theme.surfaceHover : Theme.surface

    MouseArea {
        id: area
        anchors.fill: parent
        enabled: root.clickable
        hoverEnabled: root.clickable
        cursorShape: root.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.clicked()
    }

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: Theme.spacingM
        anchors.rightMargin: Theme.spacingM
        spacing: Theme.spacingM
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.border
    }
}
