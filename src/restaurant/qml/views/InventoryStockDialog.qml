import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-I2, FR-I3: receive stock, record waste or record a stock count for one ingredient.
FormDialog {
    id: root

    property string mode: "receive" // "receive", "waste" or "count"
    property var ingredient: ({})

    readonly property string _name: ingredient.name || ""
    readonly property string _unit: ingredient.unit || ""

    signal saved(string message)

    function openFor(newMode, row) {
        mode = newMode
        ingredient = row
        qty.text = ""
        note.text = ""
        open()
    }

    function _call() {
        if (mode === "waste")
            return api.inventory.recordWaste(ingredient.id, qty.text, note.text)
        if (mode === "count")
            return api.inventory.recordCount(ingredient.id, qty.text, note.text)
        return api.inventory.receiveStock(ingredient.id, qty.text, note.text)
    }

    function save() {
        var name = _name
        var unit = _unit
        var result = _call()
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved(result.data === ""
              ? "The count for " + name + " matches, so the stock level was not changed."
              : name + ": " + result.data.delta_text + " " + unit + " recorded.")
    }

    title: (mode === "waste" ? "Record waste: " : mode === "count" ? "Stock count: " : "Receive stock: ") + _name
    confirmText: mode === "waste" ? "Record waste" : mode === "count" ? "Save count" : "Receive"
    confirmEnabled: qty.text.trim() !== ""
    onOpened: qty.focusInput()
    onSubmitted: save()

    Label {
        Layout.fillWidth: true
        text: "On hand now: " + (ingredient.on_hand_text || "") + " " + root._unit
        color: Theme.text
        wrapMode: Text.Wrap
    }

    AppTextField {
        id: qty
        Layout.fillWidth: true
        label: (root.mode === "waste" ? "Quantity wasted" : root.mode === "count" ? "Quantity counted"
                                                                                  : "Quantity received")
               + (root._unit !== "" ? " (" + root._unit + ")" : "")
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        onAccepted: note.focusInput()
    }

    AppTextField {
        id: note
        Layout.fillWidth: true
        label: "Note (optional)"
        onAccepted: {
            if (root.confirmEnabled)
                root.save()
        }
    }

    Label {
        Layout.fillWidth: true
        visible: root.mode === "count"
        text: "Any difference from the level on record is saved as an adjustment."
        color: Theme.textMuted
        wrapMode: Text.Wrap
        font.pixelSize: Theme.fontSmall
    }
}
