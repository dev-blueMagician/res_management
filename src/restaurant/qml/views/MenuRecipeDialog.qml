import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-M3: which ingredients one unit of an item uses. Read-only without edit_menu.
FormDialog {
    id: root

    readonly property bool readOnly: api.session.permissions.edit_menu !== true
    property var item: ({})
    property var ingredients: [] // active ingredients, for the add row
    property var choices: []     // those not yet in the recipe
    readonly property int _lineHeight: Theme.controlHeight + Theme.spacingS

    signal saved(string message)
    signal failed(string message)

    function openFor(menuItem) {
        item = menuItem
        lines.clear()
        addQty.text = ""
        var recipe = api.menu.getRecipe(menuItem.id)
        if (!recipe.ok) {
            failed(recipe.error)
            return
        }
        for (var i = 0; i < recipe.data.length; i++) {
            var line = recipe.data[i]
            lines.append({
                ingredientId: line.ingredient_id,
                name: line.ingredient_name,
                unit: line.unit,
                qty: line.qty_input,
                qtyText: line.qty_text,
                ingredientActive: line.ingredient_active
            })
        }
        var loadError = ""
        ingredients = []
        if (!readOnly) {
            var available = api.inventory.listIngredients(false)
            if (available.ok)
                ingredients = available.data
            else
                loadError = available.error
        }
        refreshChoices()
        open()
        errorText = loadError // after open(), which clears it
    }

    function refreshChoices() {
        var used = {}
        for (var i = 0; i < lines.count; i++)
            used[lines.get(i).ingredientId] = true
        choices = ingredients.filter(function(entry) { return !used[entry.id] })
        addIngredient.currentIndex = choices.length > 0 ? 0 : -1
    }

    function addLine() {
        var choice = choices[addIngredient.currentIndex]
        if (!choice)
            return
        lines.append({
            ingredientId: choice.id,
            name: choice.name,
            unit: choice.unit,
            qty: addQty.text.trim(),
            qtyText: addQty.text.trim(),
            ingredientActive: true
        })
        addQty.text = ""
        refreshChoices()
        lineList.positionViewAtEnd()
    }

    function removeLine(index) {
        lines.remove(index)
        refreshChoices()
    }

    function save() {
        if (addQty.text.trim() !== "")
            addLine() // typed but not yet added
        var payload = []
        for (var i = 0; i < lines.count; i++) {
            var line = lines.get(i)
            payload.push({ ingredient_id: line.ingredientId, qty: line.qty })
        }
        var result = api.menu.setRecipe(item.id, payload)
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved(result.data.length > 0
              ? "The recipe for " + item.name + " was saved."
              : item.name + " now has no recipe, so selling it deducts no stock.")
    }

    width: Math.min(620, parent ? parent.width - 2 * Theme.spacingL : 620)
    title: (readOnly ? "Recipe for " : "Edit recipe for ") + (item.name || "")
    confirmText: "Save recipe"
    onSubmitted: save()

    footer: Item {
        implicitHeight: footerButtons.implicitHeight + Theme.spacingL

        RowLayout {
            id: footerButtons
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacingL
            spacing: Theme.spacingS

            AppButton {
                text: root.readOnly ? "Close" : "Cancel"
                kind: root.readOnly ? "secondary" : "ghost"
                onClicked: root.reject()
            }

            AppButton {
                text: root.confirmText
                visible: !root.readOnly
                onClicked: root.submitted()
            }
        }
    }

    ListModel { id: lines }

    Label {
        Layout.fillWidth: true
        text: root.readOnly ? "Quantity of each ingredient used per unit sold."
                            : "Enter the quantity of each ingredient used per unit sold."
        visible: lines.count > 0 || !root.readOnly
        color: Theme.textMuted
        wrapMode: Text.Wrap
    }

    StatusChip {
        text: "No recipe: selling this item deducts no stock"
        kind: "warning"
        visible: lines.count === 0
    }

    ListView {
        id: lineList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(lines.count, 5) * root._lineHeight
        visible: lines.count > 0
        clip: true
        cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
        model: lines
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        delegate: RowLayout {
            id: lineRow

            required property int index
            required property int ingredientId
            required property string name
            required property string unit
            required property string qty
            required property string qtyText
            required property bool ingredientActive

            width: ListView.view.width
            height: root._lineHeight
            spacing: Theme.spacingS

            Label {
                Layout.fillWidth: true
                text: lineRow.name
                color: lineRow.ingredientActive ? Theme.text : Theme.textMuted
                elide: Text.ElideRight
            }

            StatusChip {
                text: "Inactive"
                visible: !lineRow.ingredientActive
            }

            AppTextField {
                Layout.fillWidth: false
                Layout.preferredWidth: 110
                visible: !root.readOnly
                text: lineRow.qty
                inputMethodHints: Qt.ImhFormattedNumbersOnly
                onTextChanged: lines.setProperty(lineRow.index, "qty", text)
            }

            Label {
                visible: root.readOnly
                text: lineRow.qtyText
                color: Theme.text
                horizontalAlignment: Text.AlignRight
                font.weight: Font.DemiBold
            }

            Label {
                Layout.preferredWidth: 56
                text: lineRow.unit
                color: Theme.textMuted
                elide: Text.ElideRight
            }

            AppButton {
                visible: !root.readOnly
                text: "Remove"
                kind: "ghost"
                onClicked: root.removeLine(lineRow.index)
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.border
        visible: !root.readOnly
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingS
        visible: !root.readOnly && root.choices.length > 0

        AppComboBox {
            id: addIngredient
            Layout.fillWidth: true
            label: "Add an ingredient"
            textRole: "label"
            valueRole: "id"
            model: root.choices.map(function(entry) {
                return { id: entry.id, label: entry.name + " (" + entry.unit + ")" }
            })
        }

        AppTextField {
            id: addQty
            Layout.fillWidth: false
            Layout.preferredWidth: 110
            label: "Quantity"
            placeholderText: "0.25"
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            onAccepted: root.addLine()
        }

        AppButton {
            Layout.alignment: Qt.AlignBottom
            text: "Add"
            kind: "secondary"
            onClicked: root.addLine()
        }
    }

    Label {
        Layout.fillWidth: true
        visible: !root.readOnly && root.choices.length === 0
        text: root.ingredients.length === 0
              ? "There are no active ingredients yet. Add them on the Inventory screen."
              : "Every active ingredient is already in this recipe."
        color: Theme.textMuted
        wrapMode: Text.Wrap
    }
}
