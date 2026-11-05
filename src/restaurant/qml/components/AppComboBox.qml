import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// A labelled drop-down. For a model of {value, label} records set
// textRole: "label" and valueRole: "value", then read currentValue.
ColumnLayout {
    id: root

    property string label: ""
    property alias model: combo.model
    property alias textRole: combo.textRole
    property alias valueRole: combo.valueRole
    property alias currentIndex: combo.currentIndex
    readonly property alias currentValue: combo.currentValue
    readonly property alias currentText: combo.currentText

    signal activated(int index)

    function selectValue(value) {
        combo.currentIndex = combo.indexOfValue(value)
    }

    spacing: Theme.spacingXs

    Label {
        text: root.label
        visible: root.label !== ""
        color: Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }

    ComboBox {
        id: combo
        Layout.fillWidth: true
        implicitWidth: 240
        implicitHeight: Theme.controlHeight
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
        Accessible.name: root.label
        onActivated: (index) => root.activated(index)

        background: Rectangle {
            radius: Theme.radius
            color: combo.hovered ? Theme.surfaceHover : Theme.surface
            border.width: combo.activeFocus ? 2 : 1
            border.color: combo.activeFocus ? Theme.accent : Theme.borderStrong
        }

        contentItem: Label {
            leftPadding: Theme.spacingM - Theme.spacingXs
            rightPadding: combo.indicator.width + Theme.spacingS
            text: combo.displayText
            font: combo.font
            color: Theme.text
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        delegate: ItemDelegate {
            required property int index
            required property var modelData
            width: ListView.view ? ListView.view.width : implicitWidth
            height: Theme.controlHeight
            highlighted: combo.highlightedIndex === index
            font: combo.font
            text: combo.textRole ? modelData[combo.textRole] : modelData

            contentItem: Label {
                text: parent.text
                font: parent.font
                color: Theme.text
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }
}
