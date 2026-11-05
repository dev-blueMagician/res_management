import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-I1: create or edit an ingredient. Stock levels change only through movements.
FormDialog {
    id: root

    property int ingredientId: 0

    signal saved(string message)

    function _fill(nameText, unitText, costText, lowText) {
        name.text = nameText
        unit.text = unitText
        cost.text = costText
        low.text = lowText
    }

    function openNew() {
        ingredientId = 0
        _fill("", "", "", "")
        open()
    }

    function openEdit(row) {
        ingredientId = row.id
        _fill(row.name, row.unit, row.unit_cost_input, row.low_input)
        open()
    }

    function save() {
        var result = ingredientId === 0
                ? api.inventory.createIngredient(name.text, unit.text, cost.text, low.text)
                : api.inventory.updateIngredient(ingredientId, {
                      name: name.text,
                      unit: unit.text,
                      unit_cost: cost.text,
                      low: low.text
                  })
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved(result.data.name + " was saved.")
    }

    title: ingredientId === 0 ? "New ingredient" : "Edit ingredient"
    confirmEnabled: name.text.trim() !== "" && unit.text.trim() !== ""
                    && cost.text.trim() !== "" && low.text.trim() !== ""
    onOpened: name.focusInput()
    onSubmitted: save()

    AppTextField {
        id: name
        Layout.fillWidth: true
        label: "Ingredient name"
        onAccepted: unit.focusInput()
    }

    AppTextField {
        id: unit
        Layout.fillWidth: true
        label: "Unit"
        placeholderText: "kg, L, bunch"
        onAccepted: cost.focusInput()
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingM

        AppTextField {
            id: cost
            Layout.fillWidth: true
            label: "Cost per unit"
            placeholderText: "1.20"
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            onAccepted: low.focusInput()
        }

        AppTextField {
            id: low
            Layout.fillWidth: true
            label: "Low-stock level"
            placeholderText: "5"
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            onAccepted: {
                if (root.confirmEnabled)
                    root.save()
            }
        }
    }

    Label {
        Layout.fillWidth: true
        visible: root.ingredientId === 0
        text: "A new ingredient starts with no stock. Use Receive to add the first delivery."
        color: Theme.textMuted
        wrapMode: Text.Wrap
        font.pixelSize: Theme.fontSmall
    }
}
