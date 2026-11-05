import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-M1 to FR-M4: categories, items and recipes. Read-only without edit_menu.
ColumnLayout {
    id: root

    objectName: "menuView"

    readonly property bool canEdit: api.session.permissions.edit_menu === true
    property var categories: []
    property var items: []
    property int selectedCategoryId: 0 // 0 means every category
    property string loadError: ""

    readonly property var selectedCategory: {
        for (var i = 0; i < categories.length; i++) {
            if (categories[i].id === selectedCategoryId)
                return categories[i]
        }
        return null
    }

    component MutedText: Label {
        color: Theme.textMuted
        elide: Text.ElideRight
        font.pixelSize: Theme.fontSmall
    }

    function reload() {
        var categoryResult = api.menu.listCategories(true)
        var itemResult = api.menu.listItems(selectedCategoryId, true)
        var failed = !categoryResult.ok ? categoryResult : !itemResult.ok ? itemResult : null
        categories = categoryResult.ok ? categoryResult.data : []
        items = itemResult.ok ? itemResult.data : []
        loadError = failed ? failed.error : ""
        if (failed)
            banner.show(failed.error, "error")
    }

    function selectCategory(id) {
        selectedCategoryId = id
        banner.clear()
        reload()
    }

    function ask(title, message, confirmText, action) {
        confirm.title = title
        confirm.message = message
        confirm.confirmText = confirmText
        confirm.action = action
        confirm.open()
    }

    function setCategoryActive(category, active) {
        if (active) {
            banner.showResult(api.menu.setCategoryActive(category.id, true),
                              category.name + " is active again.")
            return
        }
        ask("Deactivate " + category.name + "?",
            "Its items stay listed here but can't be sold until the category is active again.",
            "Deactivate",
            function() {
                banner.showResult(api.menu.setCategoryActive(category.id, false),
                                  category.name + " was deactivated.")
            })
    }

    function setItemActive(item, active) {
        if (active) {
            banner.showResult(api.menu.setItemActive(item.id, true),
                              item.name + " is active again.")
            return
        }
        ask("Deactivate " + item.name + "?",
            "It stays on past sales but can't be sold until it is active again.",
            "Deactivate",
            function() {
                banner.showResult(api.menu.setItemActive(item.id, false),
                                  item.name + " was deactivated.")
            })
    }

    Component.onCompleted: reload()
    spacing: Theme.spacingM

    Connections {
        target: api
        function onMenuChanged() { root.reload() }
    }

    PageHeader {
        Layout.fillWidth: true
        title: "Menu"
        subtitle: root.canEdit
                  ? "Items marked “No recipe” can be sold but deduct no stock."
                  : "You can view the menu. A manager makes changes. Items marked “No recipe” deduct no stock."

        AppButton {
            text: "New category"
            kind: "secondary"
            visible: root.canEdit
            onClicked: categoryDialog.openNew(root.categories.length)
        }

        AppButton {
            text: "New item"
            visible: root.canEdit
            enabled: root.categories.length > 0
            onClicked: itemDialog.openNew(root.categories, root.selectedCategoryId)
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
            Layout.preferredWidth: root.width < 900 ? 200 : 256
            Layout.fillHeight: true
            padding: 1

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                DataRow {
                    Layout.fillWidth: true
                    clickable: true
                    selected: root.selectedCategoryId === 0
                    onClicked: root.selectCategory(0)

                    Label {
                        Layout.fillWidth: true
                        text: "All categories"
                        color: Theme.text
                        font.weight: Font.DemiBold
                    }
                }

                ListView {
                    id: categoryList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
                    model: root.categories
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    delegate: DataRow {
                        required property var modelData
                        width: ListView.view.width
                        clickable: true
                        selected: root.selectedCategoryId === modelData.id
                        onClicked: root.selectCategory(modelData.id)

                        Label {
                            Layout.fillWidth: true
                            text: modelData.name
                            color: modelData.active ? Theme.text : Theme.textMuted
                            elide: Text.ElideRight
                        }

                        StatusChip {
                            text: "Inactive"
                            visible: !modelData.active
                        }
                    }

                    Label {
                        anchors.centerIn: parent
                        width: parent.width - 2 * Theme.spacingM
                        visible: categoryList.count === 0
                        text: root.loadError !== "" ? "The categories could not be loaded."
                                                    : "No categories yet."
                        color: Theme.textMuted
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Theme.border
                    visible: root.canEdit && root.selectedCategory !== null
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.margins: Theme.spacingS
                    spacing: Theme.spacingS
                    visible: root.canEdit && root.selectedCategory !== null

                    AppButton {
                        Layout.fillWidth: true
                        text: "Edit"
                        kind: "secondary"
                        onClicked: categoryDialog.openEdit(root.selectedCategory)
                    }

                    AppButton {
                        Layout.fillWidth: true
                        text: root.selectedCategory && root.selectedCategory.active
                              ? "Deactivate" : "Activate"
                        kind: "secondary"
                        onClicked: root.setCategoryActive(root.selectedCategory,
                                                          !root.selectedCategory.active)
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 1

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Theme.controlHeight
                    color: Theme.surfaceAlt

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spacingM
                        anchors.rightMargin: Theme.spacingM
                        spacing: Theme.spacingM

                        MutedText {
                            Layout.fillWidth: true
                            text: root.selectedCategory ? root.selectedCategory.name : "All items"
                            font.weight: Font.DemiBold
                        }

                        MutedText {
                            text: root.items.length === 1 ? "1 item" : root.items.length + " items"
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
                    id: itemList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    cacheBuffer: 0 // no background delegates, which warn if the view goes meanwhile
                    model: root.items
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    delegate: DataRow {
                        required property var modelData
                        width: ListView.view.width
                        implicitHeight: 64

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spacingS

                                Label {
                                    Layout.fillWidth: true
                                    Layout.maximumWidth: implicitWidth
                                    text: modelData.name
                                    color: modelData.active ? Theme.text : Theme.textMuted
                                    elide: Text.ElideRight
                                    font.weight: Font.DemiBold
                                }

                                MutedText {
                                    Layout.fillWidth: true
                                    text: modelData.category_name
                                }
                            }

                            // Three chips only just fit a 1280-wide window, so they get their own line.
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spacingS
                                visible: !modelData.has_recipe || !modelData.active || !modelData.category_active

                                StatusChip {
                                    text: "No recipe"
                                    kind: "warning"
                                    visible: !modelData.has_recipe
                                }

                                StatusChip {
                                    text: "Inactive"
                                    visible: !modelData.active
                                }

                                StatusChip {
                                    text: "Category inactive"
                                    visible: !modelData.category_active
                                }

                                Item { Layout.fillWidth: true }
                            }
                        }

                        Label {
                            Layout.preferredWidth: 96
                            text: modelData.price_text
                            color: modelData.active ? Theme.text : Theme.textMuted
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideRight
                        }

                        RowLayout {
                            Layout.fillWidth: false
                            spacing: Theme.spacingS

                            AppButton {
                                text: "Edit"
                                kind: "secondary"
                                visible: root.canEdit
                                onClicked: itemDialog.openEdit(modelData, root.categories)
                            }

                            AppButton {
                                text: "Recipe"
                                kind: "secondary"
                                onClicked: recipeDialog.openFor(modelData)
                            }

                            AppButton {
                                Layout.preferredWidth: 116
                                text: modelData.active ? "Deactivate" : "Activate"
                                kind: "ghost"
                                visible: root.canEdit
                                onClicked: root.setItemActive(modelData, !modelData.active)
                            }
                        }
                    }

                    Label {
                        anchors.centerIn: parent
                        width: parent.width - 2 * Theme.spacingXl
                        visible: itemList.count === 0
                        text: root.loadError !== "" ? "The menu could not be loaded."
                              : root.categories.length === 0
                                ? (root.canEdit ? "The menu is empty. Create a category first, then add items to it."
                                                : "The menu is empty.")
                                : root.selectedCategoryId !== 0 ? "No items in this category."
                                                                : "No items yet."
                        color: Theme.textMuted
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }
    }

    ConfirmDialog {
        id: confirm
        property var action: null
        danger: true
        onConfirmed: {
            if (action)
                action()
        }
    }

    MenuCategoryDialog {
        id: categoryDialog
        onSaved: (message) => banner.show(message, "success")
    }

    MenuItemDialog {
        id: itemDialog
        onSaved: (message) => banner.show(message, "success")
    }

    MenuRecipeDialog {
        id: recipeDialog
        onSaved: (message) => banner.show(message, "success")
        onFailed: (message) => banner.show(message, "error")
    }
}
