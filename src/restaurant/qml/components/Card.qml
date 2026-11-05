import QtQuick
import QtQuick.Layouts
import "."

// A surface with a hairline border. Children go in a padded ColumnLayout.
Rectangle {
    id: root

    default property alias content: column.data
    property int padding: Theme.spacingL

    implicitWidth: column.implicitWidth + 2 * padding
    implicitHeight: column.implicitHeight + 2 * padding
    radius: Theme.radius
    color: Theme.surface
    border.width: 1
    border.color: Theme.border

    ColumnLayout {
        id: column
        anchors.fill: parent
        anchors.margins: root.padding
        spacing: Theme.spacingM
    }
}
