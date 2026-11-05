import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// A read-only table with a fixed header. columns is a list of records:
//   {header, display (the row key to show), numeric (right-aligned), flex (share of width)}
// which is the shape api.reports.run() returns, so a report can be passed straight in.
Rectangle {
    id: root

    property var columns: []
    property var rows: []
    property string emptyText: "Nothing to show."
    property bool clickable: false

    signal rowClicked(var row, int index)

    readonly property real _totalFlex: {
        var total = 0
        for (var i = 0; i < columns.length; i++)
            total += _flex(columns[i])
        return total || 1
    }

    function _flex(column) {
        return column.flex || (column.numeric ? 1 : 2)
    }

    function _width(column) {
        return (width - 2 * Theme.spacingM) * _flex(column) / _totalFlex
    }

    function _cell(row, column) {
        var value = row[column.display || column.key]
        return value === undefined ? "" : String(value)
    }

    implicitWidth: 480
    implicitHeight: 320
    radius: Theme.radius
    color: Theme.surface
    border.width: 1
    border.color: Theme.border
    clip: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 1
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: Theme.controlHeight
            color: Theme.surfaceAlt

            Row {
                anchors.fill: parent
                anchors.leftMargin: Theme.spacingM
                anchors.rightMargin: Theme.spacingM

                Repeater {
                    model: root.columns

                    Label {
                        required property var modelData
                        width: root._width(modelData)
                        height: parent.height
                        rightPadding: Theme.spacingM
                        text: modelData.header
                        color: Theme.textMuted
                        elide: Text.ElideRight
                        horizontalAlignment: modelData.numeric ? Text.AlignRight : Text.AlignLeft
                        verticalAlignment: Text.AlignVCenter
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.border
            }
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: root.rows
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            delegate: DataRow {
                id: rowItem
                required property var modelData
                required property int index
                width: ListView.view.width
                clickable: root.clickable
                onClicked: root.rowClicked(modelData, index)

                Row {
                    Layout.fillWidth: true

                    Repeater {
                        model: root.columns

                        Label {
                            required property var modelData
                            width: root._width(modelData)
                            rightPadding: Theme.spacingM
                            text: root._cell(rowItem.modelData, modelData)
                            color: Theme.text
                            elide: Text.ElideRight
                            horizontalAlignment: modelData.numeric ? Text.AlignRight : Text.AlignLeft
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                        }
                    }
                }
            }

            Label {
                anchors.centerIn: parent
                visible: list.count === 0
                text: root.emptyText
                color: Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontBody
            }
        }
    }
}
