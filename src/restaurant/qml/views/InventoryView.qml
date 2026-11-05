import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-I1 to FR-I5: ingredients on the left, the chosen one's actions and history on the right.
ColumnLayout {
    id: root

    objectName: "inventoryView"

    readonly property int _historyPage: 50
    readonly property bool narrow: width < 900 // the smallest windows drop what the side panel shows
    readonly property int _onHandWidth: 88
    readonly property int _unitWidth: 48
    readonly property int _lowWidth: 64
    readonly property int _costWidth: 72
    readonly property int _statusWidth: 96

    property var ingredients: []
    property var lowById: ({})   // id -> low-stock row: the bridge decides what is low
    property int lowCount: 0
    property bool lowOnly: false
    property int selectedId: 0
    property string loadError: ""
    property var movements: []
    property bool moreMovements: false
    property string movementError: ""

    readonly property var rows: lowOnly
                                ? ingredients.filter(function(row) { return lowById[row.id] !== undefined })
                                : ingredients
    readonly property var selected: {
        for (var i = 0; i < ingredients.length; i++) {
            if (ingredients[i].id === selectedId)
                return ingredients[i]
        }
        return null
    }

    component HeaderText: Label {
        color: Theme.textMuted
        elide: Text.ElideRight
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }

    component Fact: ColumnLayout {
        property string label: ""
        property string value: ""
        Layout.fillWidth: false
        spacing: 0

        Label {
            text: parent.label
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }

        Label {
            text: parent.value
            color: Theme.text
            font.weight: Font.DemiBold
        }
    }

    function statusText(row) {
        if (!row.active)
            return "Inactive"
        var low = lowById[row.id]
        return low === undefined ? "OK" : low.is_negative ? "! Below zero" : "! Low"
    }

    function statusKind(row) {
        if (!row.active)
            return "neutral"
        var low = lowById[row.id]
        return low === undefined ? "neutral" : low.is_negative ? "danger" : "warning"
    }

    function isFlagged(row) {
        return row.active && lowById[row.id] !== undefined
    }

    function reload() {
        var list = api.inventory.listIngredients(true)
        var low = api.inventory.listLowStock()
        var failed = !list.ok ? list : !low.ok ? low : null
        loadError = failed ? failed.error : ""
        if (failed)
            banner.show(failed.error, "error")
        var byId = {}
        var lowRows = low.ok ? low.data : []
        for (var i = 0; i < lowRows.length; i++)
            byId[lowRows[i].id] = lowRows[i]
        lowById = byId
        lowCount = lowRows.length
        ingredients = list.ok ? list.data : []
        if (selected === null || (lowOnly && lowById[selectedId] === undefined))
            selectedId = rows.length > 0 ? rows[0].id : 0
        loadMovements(true)
    }

    function setLowOnly(wanted) {
        lowOnly = wanted
        banner.clear()
        reload()
    }

    function select(id) {
        selectedId = id
        banner.clear()
        loadMovements(true)
    }

    function loadMovements(fromStart) {
        if (selectedId === 0) {
            movements = []
            moreMovements = false
            movementError = ""
            return
        }
        var offset = fromStart ? 0 : movements.length
        // One more than a page, to know whether there is another page.
        var result = api.inventory.listMovements(selectedId, "", "", _historyPage + 1, offset)
        if (!result.ok) {
            movementError = result.error
            banner.show(result.error, "error")
            movements = fromStart ? [] : movements
            moreMovements = false
            return
        }
        movementError = ""
        moreMovements = result.data.length > _historyPage
        var page = result.data.slice(0, _historyPage)
        movements = fromStart ? page : movements.concat(page)
    }

    function setActive(row, active) {
        if (active) {
            banner.showResult(api.inventory.setIngredientActive(row.id, true),
                              row.name + " is active again.")
            return
        }
        confirm.title = "Deactivate " + row.name + "?"
        confirm.message = "Its stock can't be changed and it can't be added to recipes until it "
                + "is active again. Its history is kept."
        confirm.ingredientId = row.id
        confirm.ingredientName = row.name
        confirm.open()
    }

    Component.onCompleted: reload()
    spacing: Theme.spacingM

    Connections {
        target: api
        function onStockChanged() { root.reload() }
    }

    PageHeader {
        Layout.fillWidth: true
        title: "Inventory"
        subtitle: root.loadError !== "" ? ""
                  : root.lowCount === 0 ? "Nothing is at or below its low-stock level."
                  : root.lowCount === 1 ? "1 ingredient is at or below its low-stock level."
                  : root.lowCount + " ingredients are at or below their low-stock level."

        AppButton {
            text: "New ingredient"
            onClicked: ingredientDialog.openNew()
        }
    }

    MessageBanner {
        id: banner
        Layout.fillWidth: true
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Theme.spacingM

        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 1

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.margins: Theme.spacingS
                    spacing: Theme.spacingS

                    AppButton {
                        text: "All ingredients"
                        kind: root.lowOnly ? "ghost" : "secondary"
                        onClicked: root.setLowOnly(false)
                    }

                    AppButton {
                        text: "Low stock (" + root.lowCount + ")"
                        kind: root.lowOnly ? "secondary" : "ghost"
                        onClicked: root.setLowOnly(true)
                    }

                    Item { Layout.fillWidth: true }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Theme.controlHeight
                    color: Theme.surfaceAlt

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spacingM
                        anchors.rightMargin: Theme.spacingM
                        spacing: Theme.spacingM

                        HeaderText {
                            Layout.fillWidth: true
                            text: "Ingredient"
                        }

                        HeaderText {
                            Layout.preferredWidth: root._onHandWidth
                            text: "On hand"
                            horizontalAlignment: Text.AlignRight
                        }

                        HeaderText {
                            Layout.preferredWidth: root._unitWidth
                            text: "Unit"
                        }

                        HeaderText {
                            visible: !root.narrow
                            Layout.preferredWidth: root._lowWidth
                            text: "Low at"
                            horizontalAlignment: Text.AlignRight
                        }

                        HeaderText {
                            visible: !root.narrow
                            Layout.preferredWidth: root._costWidth
                            text: "Unit cost"
                            horizontalAlignment: Text.AlignRight
                        }

                        HeaderText {
                            Layout.preferredWidth: root._statusWidth
                            text: "Status"
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
                    id: ingredientList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
                    model: root.rows
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    delegate: DataRow {
                        required property var modelData
                        width: ListView.view.width
                        clickable: true
                        selected: root.selectedId === modelData.id
                        onClicked: root.select(modelData.id)

                        Label {
                            Layout.fillWidth: true
                            text: modelData.name
                            color: modelData.active ? Theme.text : Theme.textMuted
                            elide: Text.ElideRight
                        }

                        Label {
                            Layout.preferredWidth: root._onHandWidth
                            text: modelData.on_hand_text
                            color: !modelData.active ? Theme.textMuted
                                   : root.statusKind(modelData) === "danger" ? Theme.danger
                                   : root.statusKind(modelData) === "warning" ? Theme.warning : Theme.text
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideRight
                            font.weight: root.isFlagged(modelData) ? Font.Bold : Font.Normal
                        }

                        Label {
                            Layout.preferredWidth: root._unitWidth
                            text: modelData.unit
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }

                        Label {
                            visible: !root.narrow
                            Layout.preferredWidth: root._lowWidth
                            text: modelData.low_text
                            color: Theme.textMuted
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideRight
                        }

                        Label {
                            visible: !root.narrow
                            Layout.preferredWidth: root._costWidth
                            text: modelData.unit_cost_text
                            color: Theme.textMuted
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideRight
                        }

                        Item {
                            Layout.preferredWidth: root._statusWidth
                            implicitHeight: statusChip.implicitHeight

                            StatusChip {
                                id: statusChip
                                anchors.verticalCenter: parent.verticalCenter
                                text: root.statusText(modelData)
                                kind: root.statusKind(modelData)
                            }
                        }
                    }

                    Label {
                        anchors.centerIn: parent
                        width: parent.width - 2 * Theme.spacingXl
                        visible: ingredientList.count === 0
                        text: root.loadError !== "" ? "The ingredients could not be loaded."
                              : root.lowOnly ? "Nothing is low on stock."
                              : "No ingredients yet. Add the first one with New ingredient."
                        color: Theme.textMuted
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }

        Card {
            Layout.preferredWidth: root.narrow ? 340 : 392
            Layout.fillHeight: true
            padding: Theme.spacingM

            Label {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: root.selected === null
                text: root.ingredients.length === 0
                      ? "Stock and history appear here once there is an ingredient."
                      : "Select an ingredient to see its stock and history."
                color: Theme.textMuted
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: root.selected !== null
                spacing: Theme.spacingM

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingS

                    Label {
                        Layout.fillWidth: true
                        text: root.selected ? root.selected.name : ""
                        color: Theme.text
                        elide: Text.ElideRight
                        font.pixelSize: Theme.fontLarge
                        font.weight: Font.DemiBold
                    }

                    StatusChip {
                        text: root.selected ? root.statusText(root.selected) : ""
                        kind: root.selected ? root.statusKind(root.selected) : "neutral"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingL

                    Fact {
                        label: "On hand"
                        value: root.selected ? root.selected.on_hand_text + " " + root.selected.unit : ""
                    }

                    Fact {
                        label: "Low at"
                        value: root.selected ? root.selected.low_text + " " + root.selected.unit : ""
                    }

                    Fact {
                        label: "Cost per " + (root.selected ? root.selected.unit : "unit")
                        value: root.selected ? root.selected.unit_cost_text : ""
                    }

                    Item { Layout.fillWidth: true }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: 3
                    columnSpacing: Theme.spacingS
                    rowSpacing: Theme.spacingS
                    uniformCellWidths: true

                    AppButton {
                        Layout.fillWidth: true
                        text: "Receive"
                        enabled: root.selected !== null && root.selected.active
                        onClicked: stockDialog.openFor("receive", root.selected)
                    }

                    AppButton {
                        Layout.fillWidth: true
                        text: "Waste"
                        kind: "secondary"
                        enabled: root.selected !== null && root.selected.active
                        onClicked: stockDialog.openFor("waste", root.selected)
                    }

                    AppButton {
                        Layout.fillWidth: true
                        text: "Count"
                        kind: "secondary"
                        enabled: root.selected !== null && root.selected.active
                        onClicked: stockDialog.openFor("count", root.selected)
                    }

                    AppButton {
                        Layout.fillWidth: true
                        text: "Edit"
                        kind: "secondary"
                        onClicked: ingredientDialog.openEdit(root.selected)
                    }

                    AppButton {
                        Layout.fillWidth: true
                        Layout.columnSpan: 2
                        text: root.selected && !root.selected.active ? "Activate" : "Deactivate"
                        kind: "ghost"
                        onClicked: root.setActive(root.selected, !root.selected.active)
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: Theme.spacingS

                    Label {
                        text: "Movement history"
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 1
                        color: Theme.border
                    }

                    ListView {
                        id: movementList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
                        model: root.movements
                        spacing: Theme.spacingS
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}

                        delegate: ColumnLayout {
                            required property var modelData
                            width: ListView.view.width - Theme.spacingS
                            spacing: 0

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spacingS

                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.movement_type_text
                                    color: Theme.text
                                    elide: Text.ElideRight
                                }

                                Label {
                                    text: modelData.delta_text + " " + modelData.unit
                                    color: Theme.text
                                    font.weight: Font.DemiBold
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: modelData.created_at_text + " · " + modelData.full_name
                                color: Theme.textMuted
                                elide: Text.ElideRight
                                font.pixelSize: Theme.fontSmall
                            }

                            Label {
                                Layout.fillWidth: true
                                visible: modelData.note !== ""
                                text: modelData.note
                                color: Theme.textMuted
                                wrapMode: Text.Wrap
                                font.pixelSize: Theme.fontSmall
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            width: parent.width - 2 * Theme.spacingM
                            visible: movementList.count === 0
                            text: root.movementError !== "" ? "The history could not be loaded."
                                                            : "No stock movements yet."
                            color: Theme.textMuted
                            wrapMode: Text.Wrap
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }

                    AppButton {
                        Layout.fillWidth: true
                        visible: root.moreMovements
                        text: "Show older movements"
                        kind: "secondary"
                        onClicked: root.loadMovements(false)
                    }
                }
            }
        }
    }

    ConfirmDialog {
        id: confirm
        property int ingredientId: 0
        property string ingredientName: ""
        confirmText: "Deactivate"
        danger: true
        onConfirmed: banner.showResult(api.inventory.setIngredientActive(ingredientId, false),
                                       ingredientName + " was deactivated.")
    }

    InventoryIngredientDialog {
        id: ingredientDialog
        onSaved: (message) => banner.show(message, "success")
    }

    InventoryStockDialog {
        id: stockDialog
        onSaved: (message) => banner.show(message, "success")
    }
}
