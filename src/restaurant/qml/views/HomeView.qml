import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// What matters today, for this role. Sales figures only appear for roles that may see them.
ColumnLayout {
    id: root

    objectName: "homeView"

    readonly property var permissions: api.session.permissions
    readonly property bool canSeeSales: permissions.view_sales_reports === true
    readonly property bool canSeeStock: permissions.view_stock_reports === true
    readonly property bool canSell: permissions.record_sales === true

    property int lowCount: 0
    property int belowZeroCount: 0
    property int ingredientCount: 0
    property string stockValue: ""
    property var salesToday: ({})
    property var topItems: []
    property var lowItems: []
    property var cart: ({})

    readonly property var quickLinks: {
        var blurbs = {
            sales: "Ring up sales, see recent ones",
            menu: "Items, prices and recipes",
            inventory: "Receive, count and adjust stock",
            reports: "Sales and stock figures, CSV export"
        }
        var links = []
        var sections = api.session.sections
        for (var i = 0; i < sections.length; i++) {
            var blurb = blurbs[sections[i].key]
            if (blurb)
                links.push({key: sections[i].key, label: sections[i].label, blurb: blurb})
        }
        return links
    }

    function plural(count, word) {
        return count + " " + word + (count === 1 ? "" : "s")
    }

    function dataOf(result, fallback) {
        if (result.ok)
            return result.data
        banner.show(result.error, "error")
        return fallback
    }

    function reload() {
        banner.clear()
        lowCount = dataOf(api.inventory.lowStockCount(), 0)
        if (canSeeSales) {
            var today = dataOf(api.reports.localDate(0), "")
            var range = {start_date: today, end_date: today}
            salesToday = dataOf(api.reports.run("sales_summary", range), {summary: {}}).summary
            topItems = dataOf(api.reports.run("sales_by_item", range), {rows: []}).rows.slice(0, 5)
        }
        if (canSeeStock) {
            lowItems = dataOf(api.reports.run("low_stock", {}), {rows: []}).rows
            belowZeroCount = lowItems.filter(row => row.is_negative).length
            stockValue = dataOf(api.reports.run("stock_value", {}), {summary: {}}).summary.total_value_text || ""
            ingredientCount = dataOf(api.inventory.listIngredients(false), []).length
        }
        if (canSell)
            cart = dataOf(api.sales.cart(), {})
    }

    function navigate(result) {
        banner.showResult(result)
    }

    spacing: Theme.spacingL

    Component.onCompleted: reload()

    Connections {
        target: api
        function onSalesChanged() { root.reload() }
        function onStockChanged() { root.reload() }
    }

    component QuickLink: AbstractButton {
        id: link

        property string blurb: ""

        hoverEnabled: true
        padding: Theme.spacingM
        implicitHeight: linkColumn.implicitHeight + 2 * padding
        implicitWidth: 200
        Accessible.name: text

        contentItem: ColumnLayout {
            id: linkColumn
            spacing: 2

            Label {
                Layout.fillWidth: true
                text: link.text
                color: Theme.accent
                elide: Text.ElideRight
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontLarge
                font.weight: Font.DemiBold
            }

            Label {
                Layout.fillWidth: true
                text: link.blurb
                color: Theme.textMuted
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
            }

            Item {
                Layout.fillHeight: true
            }
        }

        background: Rectangle {
            radius: Theme.radius
            color: link.down ? Theme.accentSoft : link.hovered ? Theme.surfaceHover : Theme.surface
            border.width: link.visualFocus ? 2 : 1
            border.color: link.visualFocus ? Theme.accent : Theme.border
        }
    }

    PageHeader {
        Layout.fillWidth: true
        title: api.session.user.full_name !== "" ? "Hello, " + api.session.user.full_name : "Home"
        subtitle: Qt.formatDate(new Date(), "dddd, d MMMM yyyy")

        AppButton {
            text: "New sale"
            visible: root.canSell && root.canSeeStock
            large: true
            objectName: "homeNewSale"
            onClicked: root.navigate(api.session.goTo("sales"))
        }
    }

    MessageBanner {
        id: banner
        objectName: "homeBanner"
        Layout.fillWidth: true
    }

    // The cashier's page is mostly one big way into the till.
    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.maximumHeight: 760
        visible: root.canSell && !root.canSeeStock
        spacing: Theme.spacingM

        AbstractButton {
            id: startSale

            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 3
            objectName: "homeStartSale"
            hoverEnabled: true
            padding: Theme.spacingL
            text: root.cart.item_count > 0 ? "Back to the current sale" : "Start a sale"
            onClicked: root.navigate(api.session.goTo("sales"))

            contentItem: ColumnLayout {
                spacing: Theme.spacingXs

                Item {
                    Layout.fillHeight: true
                }

                Label {
                    Layout.fillWidth: true
                    text: startSale.text
                    color: Theme.textOnAccent
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontDisplay
                    font.weight: Font.DemiBold
                }

                Label {
                    Layout.fillWidth: true
                    text: "Tap items, choose how they paid, confirm."
                    color: Theme.textOnAccent
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                }

                Item {
                    Layout.fillHeight: true
                }
            }

            background: Rectangle {
                radius: Theme.radius
                color: startSale.down ? Theme.accentPressed
                       : startSale.hovered ? Theme.accentHover : Theme.accent
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 2
            spacing: Theme.spacingM

            ReportsStatTile {
                Layout.fillWidth: true
                Layout.fillHeight: true
                objectName: "homeCurrentSale"
                label: "Current sale"
                value: root.cart.item_count > 0 ? (root.cart.total_text || "") : "Empty"
                note: root.cart.item_count > 0 ? root.plural(root.cart.item_count, "item") + " waiting"
                                               : "Nothing rung up yet"
            }

            ReportsStatTile {
                Layout.fillWidth: true
                Layout.fillHeight: true
                objectName: "homeLowStockCount"
                label: "Low-stock ingredients"
                value: String(root.lowCount)
                tone: root.lowCount > 0 ? "warning" : "neutral"
                note: "At or below their low-stock level"
            }

            QuickLink {
                Layout.fillWidth: true
                Layout.fillHeight: true
                objectName: "homeLink-menu"
                text: "Menu"
                blurb: "See items and prices"
                onClicked: root.navigate(api.session.goTo("menu"))
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: root.canSeeStock
        spacing: Theme.spacingM

        ReportsStatTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 112
            visible: root.canSeeSales
            objectName: "homeSalesToday"
            label: "Sales today, with tax"
            value: root.salesToday.total_text || "-"
            note: root.plural(root.salesToday.sale_count || 0, "sale")
        }

        ReportsStatTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 112
            visible: root.canSeeSales
            objectName: "homeVoidsToday"
            label: "Voided today"
            value: String(root.salesToday.void_count || 0)
            note: (root.salesToday.voided_total_text || "-") + " not in sales"
        }

        ReportsStatTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 112
            objectName: "homeLowStock"
            label: "Low-stock ingredients"
            value: String(root.lowCount)
            tone: root.lowCount > 0 ? "warning" : "neutral"
            clickable: true
            note: "View the list"
            onClicked: root.navigate(api.reports.openReport("low_stock"))
        }

        ReportsStatTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 112
            visible: root.canSeeStock && !root.canSeeSales
            objectName: "homeBelowZero"
            label: "Below zero"
            value: String(root.belowZeroCount)
            tone: root.belowZeroCount > 0 ? "danger" : "neutral"
            note: root.belowZeroCount > 0 ? "Check these with a stock count" : "No negative levels"
        }

        ReportsStatTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 112
            visible: root.canSeeStock
            objectName: "homeStockValue"
            label: "Stock value"
            value: root.stockValue || "-"
            note: "At unit cost"
        }

        ReportsStatTile {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 112
            visible: root.canSeeStock && !root.canSeeSales
            objectName: "homeIngredients"
            label: "Ingredients tracked"
            value: String(root.ingredientCount)
            note: "Active ingredients"
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: root.canSeeStock
        Layout.minimumHeight: root.canSeeStock ? 140 : 0
        visible: root.canSeeStock
        spacing: Theme.spacingM

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 1
            visible: root.canSeeSales
            spacing: Theme.spacingS

            Label {
                Layout.preferredHeight: Theme.controlHeight
                text: "Top sellers today"
                color: Theme.text
                verticalAlignment: Text.AlignVCenter
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontLarge
                font.weight: Font.DemiBold
            }

            DataTable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                objectName: "homeTopItems"
                emptyText: "No sales yet today."
                rows: root.topItems
                columns: [
                    {header: "Item", display: "name", flex: 3},
                    {header: "Qty", display: "quantity", numeric: true},
                    {header: "Excl. tax", display: "total_text", numeric: true, flex: 1.2}
                ]
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 1
            spacing: Theme.spacingS

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingS

                Label {
                    Layout.fillWidth: true
                    text: "Low stock"
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontLarge
                    font.weight: Font.DemiBold
                }

                AppButton {
                    text: "View full list"
                    kind: "ghost"
                    objectName: "homeLowStockLink"
                    onClicked: root.navigate(api.reports.openReport("low_stock"))
                }
            }

            DataTable {
                Layout.fillWidth: true
                Layout.fillHeight: true
                objectName: "homeLowItems"
                emptyText: "Nothing is low on stock."
                rows: root.lowItems
                columns: [
                    {header: "Ingredient", display: "name", flex: 2.4},
                    {header: "On hand", display: "on_hand_text", numeric: true, flex: 1.4},
                    {header: "Unit", display: "unit", flex: 1.2},
                    {header: "Status", display: "status", flex: 1.4}
                ]
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.preferredHeight: 84
        Layout.maximumHeight: 84
        // The sidebar has the same links, so they go first when the window is very short.
        visible: root.canSeeStock && root.height >= 600
        spacing: Theme.spacingM

        Repeater {
            model: root.quickLinks

            QuickLink {
                required property var modelData
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                objectName: "homeLink-" + modelData.key
                text: modelData.label
                blurb: modelData.blurb
                onClicked: root.navigate(api.session.goTo(modelData.key))
            }
        }
    }

    Item {
        Layout.fillHeight: true
        visible: !root.canSeeStock && root.height > 880
    }
}
