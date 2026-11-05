import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Create or edit a menu category.
FormDialog {
    id: root

    property int categoryId: 0

    signal saved(string message)

    function openNew(nextSortOrder) {
        categoryId = 0
        name.text = ""
        sortOrder.text = String(nextSortOrder)
        open()
    }

    function openEdit(category) {
        categoryId = category.id
        name.text = category.name
        sortOrder.text = String(category.sort_order)
        open()
    }

    function save() {
        var result = categoryId === 0
                ? api.menu.createCategory(name.text, sortOrder.text)
                : api.menu.updateCategory(categoryId, { name: name.text, sort_order: sortOrder.text })
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved(result.data.name + " was saved.")
    }

    title: categoryId === 0 ? "New category" : "Edit category"
    confirmEnabled: name.text.trim() !== ""
    onOpened: name.focusInput()
    onSubmitted: save()

    AppTextField {
        id: name
        Layout.fillWidth: true
        label: "Category name"
        onAccepted: sortOrder.focusInput()
    }

    AppTextField {
        id: sortOrder
        Layout.fillWidth: true
        label: "Position in the list (lower numbers come first)"
        onAccepted: {
            if (root.confirmEnabled)
                root.save()
        }
    }
}
