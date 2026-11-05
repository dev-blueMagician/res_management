import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// The till: item tiles on the left and the current sale on the right, with recent
// sales one tap away. The cart and its totals live in the bridge (FR-P1 to FR-P3, FR-P6).
ColumnLayout {
    id: root

    objectName: "salesView"

    readonly property var emptyCart: ({
        lines: [],
        item_count: 0,
        subtotal_text: "",
        tax_rate_text: "",
        tax_text: "",
        total_text: ""
    })
    property var cart: emptyCart
    property var lastSale: null
    property string page: "sell"

    function refreshCart() {
        if (!api.session.signedIn) {
            cart = emptyCart
            return
        }
        var result = api.sales.cart()
        if (!result.ok) {
            cart = emptyCart
            banner.show(result.error, "error")
            return
        }
        cart = result.data
        if (cart.item_count > 0)
            lastSale = null
        banner.clear()
    }

    function checkout(method) {
        var result = api.sales.checkout(method)
        if (banner.showResult(result))
            lastSale = result.data
    }

    spacing: Theme.spacingM

    Component.onCompleted: refreshCart()

    Connections {
        target: api.sales
        function onCartChanged() { root.refreshCart() }
    }

    Connections {
        target: api
        function onMenuChanged() { root.refreshCart() }
    }

    PageHeader {
        Layout.fillWidth: true
        title: "Sales"

        AppButton {
            objectName: "salesPageSell"
            text: root.cart.item_count > 0 ? "New sale (" + root.cart.item_count + ")" : "New sale"
            kind: root.page === "sell" ? "primary" : "secondary"
            onClicked: root.page = "sell"
        }

        AppButton {
            objectName: "salesPageRecent"
            text: "Recent sales"
            kind: root.page === "recent" ? "primary" : "secondary"
            onClicked: {
                banner.clear()
                root.page = "recent"
            }
        }
    }

    MessageBanner {
        id: banner
        objectName: "salesBanner"
        Layout.fillWidth: true
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: root.page === "sell" ? 0 : 1

        RowLayout {
            spacing: Theme.spacingM

            SalesMenuPanel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                lines: root.cart.lines
                onFailed: message => banner.show(message, "error")
            }

            SalesCartPanel {
                Layout.fillHeight: true
                Layout.preferredWidth: Math.min(460, Math.max(372, root.width * 0.3))
                cart: root.cart
                lastSale: root.lastSale
                onFailed: message => banner.show(message, "error")
                onCheckoutRequested: method => root.checkout(method)
            }
        }

        SalesRecentPanel {
            onFailed: message => banner.show(message, "error")
            onSucceeded: message => banner.show(message, "success")
        }
    }
}
