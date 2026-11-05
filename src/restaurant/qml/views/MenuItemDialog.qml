import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Create or edit a menu item.
FormDialog {
    id: root

    property int itemId: 0

    signal saved(string message)

    function _useCategories(categories, selectedId) {
        category.model = categories.map(function(entry) {
            return { value: entry.id, label: entry.active ? entry.name : entry.name + " (inactive)" }
        })
        category.currentIndex = 0
        if (selectedId)
            category.selectValue(selectedId)
    }

    function openNew(categories, selectedId) {
        itemId = 0
        name.text = ""
        price.text = ""
        _useCategories(categories, selectedId)
        open()
    }

    function openEdit(item, categories) {
        itemId = item.id
        name.text = item.name
        price.text = item.price_input
        _useCategories(categories, item.category_id)
        open()
    }

    function save() {
        var result = itemId === 0
                ? api.menu.createItem(category.currentValue, name.text, price.text)
                : api.menu.updateItem(itemId, {
                      name: name.text,
                      category_id: category.currentValue,
                      price: price.text
                  })
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved(result.data.name + " was saved at " + result.data.price_text + ".")
    }

    title: itemId === 0 ? "New menu item" : "Edit menu item"
    confirmEnabled: name.text.trim() !== "" && price.text.trim() !== ""
    onOpened: name.focusInput()
    onSubmitted: save()

    AppTextField {
        id: name
        Layout.fillWidth: true
        label: "Item name"
        onAccepted: price.focusInput()
    }

    AppComboBox {
        id: category
        Layout.fillWidth: true
        label: "Category"
        textRole: "label"
        valueRole: "value"
        model: []
    }

    AppTextField {
        id: price
        Layout.fillWidth: true
        label: "Price before tax"
        placeholderText: "12.50"
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        onAccepted: {
            if (root.confirmEnabled)
                root.save()
        }
    }
}
