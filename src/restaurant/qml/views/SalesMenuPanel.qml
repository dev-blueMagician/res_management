import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Category tabs and a grid of large tiles. A tap adds one of the item to the sale.
ColumnLayout {
    id: root

    property var lines: []
    property var categories: []
    property var items: []
    property int categoryId: 0

    signal failed(string message)

    readonly property var shownItems: items.filter(
        item => categoryId === 0 || item.category_id === categoryId)
    readonly property var quantities: {
        var byItem = {}
        for (var i = 0; i < lines.length; i++)
            byItem[lines[i].menu_item_id] = lines[i].quantity
        return byItem
    }

    function reload() {
        var categoryResult = api.menu.listCategories(false)
        var itemResult = api.menu.listSellableItems()
        if (!categoryResult.ok || !itemResult.ok) {
            categories = []
            items = []
            failed((categoryResult.ok ? itemResult : categoryResult).error)
            return
        }
        var used = {}
        itemResult.data.forEach(item => used[item.category_id] = true)
        items = itemResult.data
        categories = [{id: 0, name: "All"}].concat(categoryResult.data.filter(c => used[c.id]))
        if (!used[categoryId])
            categoryId = 0
    }

    spacing: Theme.spacingS

    Component.onCompleted: reload()

    Connections {
        target: api
        function onMenuChanged() { root.reload() }
    }

    ListView {
        Layout.fillWidth: true
        Layout.preferredHeight: 52
        visible: root.items.length > 0
        orientation: ListView.Horizontal
        spacing: Theme.spacingS
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: root.categories

        delegate: AbstractButton {
            id: tab

            required property var modelData
            readonly property bool current: root.categoryId === modelData.id

            objectName: "salesTab-" + modelData.name
            height: ListView.view.height
            implicitWidth: Math.max(96, tabLabel.implicitWidth + 2 * Theme.spacingL)
            hoverEnabled: true
            text: modelData.name
            onClicked: root.categoryId = modelData.id

            contentItem: Label {
                id: tabLabel
                text: tab.text
                color: tab.current ? Theme.textOnAccent : Theme.text
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
            }

            background: Rectangle {
                radius: Theme.radius
                color: tab.current ? Theme.sidebarActive
                       : tab.down ? Theme.border : tab.hovered ? Theme.surfaceHover : Theme.surface
                border.width: tab.current ? 0 : 1
                border.color: Theme.border
            }
        }
    }

    GridView {
        id: grid

        readonly property int columns: Math.max(2, Math.floor(width / 160))

        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: root.items.length > 0
        clip: true
        cellWidth: Math.max(1, Math.floor(width / columns))
        cellHeight: 112
        boundsBehavior: Flickable.StopAtBounds
        model: root.shownItems
        ScrollBar.vertical: ScrollBar {}

        delegate: Item {
            id: cell

            required property var modelData
            readonly property int inSale: root.quantities[modelData.id] || 0

            width: grid.cellWidth
            height: grid.cellHeight

            AbstractButton {
                id: tile

                anchors.fill: parent
                anchors.rightMargin: Theme.spacingS
                anchors.bottomMargin: Theme.spacingS
                objectName: "salesTile-" + cell.modelData.name
                hoverEnabled: true
                padding: Theme.spacingM
                topPadding: Theme.spacingS + Theme.spacingXs
                bottomPadding: Theme.spacingS + Theme.spacingXs
                Accessible.name: cell.modelData.name + ", " + cell.modelData.price_text
                onClicked: {
                    var result = api.sales.cartAdd(cell.modelData.id)
                    if (!result.ok) {
                        root.failed(result.error)
                        root.reload()
                    }
                }

                contentItem: ColumnLayout {
                    spacing: Theme.spacingXs

                    Label {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: cell.modelData.name
                        color: Theme.text
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    Label {
                        text: cell.modelData.price_text
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                    }
                }

                background: Rectangle {
                    radius: Theme.radius
                    color: tile.down ? Theme.accentSoft : tile.hovered ? Theme.surfaceHover : Theme.surface
                    border.width: cell.inSale > 0 || tile.visualFocus ? 2 : 1
                    border.color: cell.inSale > 0 || tile.visualFocus ? Theme.accent : Theme.border
                }

                Rectangle {
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: Theme.spacingS
                    width: Math.max(32, badge.implicitWidth + Theme.spacingM)
                    height: 32
                    radius: 16
                    visible: cell.inSale > 0
                    color: Theme.accent

                    Label {
                        id: badge
                        anchors.centerIn: parent
                        text: String(cell.inSale)
                        color: Theme.textOnAccent
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                }
            }
        }
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: root.items.length === 0

        Label {
            objectName: "salesMenuEmpty"
            anchors.centerIn: parent
            width: Math.min(parent.width - 2 * Theme.spacingL, 420)
            text: "There is nothing on the menu to sell yet. A manager can add items on the Menu screen."
            color: Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }
    }
}
