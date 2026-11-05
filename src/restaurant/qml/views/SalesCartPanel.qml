import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// The current sale: its lines, the bridge's totals, payment method and confirm.
Card {
    id: root

    property var cart: ({})
    property var lastSale: null
    property string paymentMethod: ""
    property var paymentMethods: []

    signal failed(string message)
    signal checkoutRequested(string method)

    readonly property bool hasItems: (cart.item_count || 0) > 0

    // A ListModel updated in place, so a quantity change keeps the list's scroll position.
    function syncLines() {
        var lines = cart.lines || []
        var grew = lines.length > lineModel.count
        for (var i = 0; i < lines.length; i++) {
            if (i < lineModel.count)
                lineModel.set(i, lines[i])
            else
                lineModel.append(lines[i])
        }
        while (lineModel.count > lines.length)
            lineModel.remove(lineModel.count - 1)
        if (grew)
            lineList.positionViewAtEnd()
        if (lines.length === 0)
            paymentMethod = ""
    }

    function report(result) {
        if (!result.ok)
            failed(result.error)
    }

    padding: Theme.spacingM
    onCartChanged: syncLines()

    Component.onCompleted: {
        var result = api.sales.paymentMethods()
        if (result.ok)
            paymentMethods = result.data
        else
            failed(result.error)
    }

    ListModel {
        id: lineModel
    }

    ConfirmDialog {
        id: clearDialog
        objectName: "clearSaleDialog"
        title: "Clear this sale?"
        message: "All items will be removed from the current sale."
        confirmText: "Clear sale"
        cancelText: "Keep sale"
        danger: true
        onConfirmed: root.report(api.sales.cartClear())
    }

    component TotalRow: RowLayout {
        id: totalRow

        property string label: ""
        property string value: ""
        property bool strong: false

        spacing: Theme.spacingS

        Label {
            Layout.fillWidth: true
            text: totalRow.label
            color: totalRow.strong ? Theme.text : Theme.textMuted
            font.family: Theme.fontFamily
            font.pixelSize: totalRow.strong ? Theme.fontLarge : Theme.fontBody
            font.weight: totalRow.strong ? Font.DemiBold : Font.Normal
        }

        Label {
            text: totalRow.value
            color: Theme.text
            font.family: Theme.fontFamily
            font.pixelSize: totalRow.strong ? Theme.fontTitle : Theme.fontBody
            font.weight: totalRow.strong ? Font.DemiBold : Font.Normal
        }
    }

    component QuantityButton: AbstractButton {
        id: quantityButton

        property bool quiet: false

        implicitWidth: 48
        implicitHeight: 48
        hoverEnabled: true

        contentItem: Label {
            text: quantityButton.text
            color: quantityButton.quiet ? Theme.textMuted : Theme.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.family: Theme.fontFamily
            font.pixelSize: quantityButton.quiet ? Theme.fontBody : Theme.fontTitle
            font.weight: quantityButton.quiet ? Font.Normal : Font.DemiBold
        }

        background: Rectangle {
            radius: Theme.radius
            color: quantityButton.down ? Theme.border
                   : quantityButton.hovered ? Theme.surfaceHover
                   : quantityButton.quiet ? "transparent" : Theme.surfaceAlt
            border.width: quantityButton.quiet ? 0 : 1
            border.color: Theme.borderStrong
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingS

        Label {
            Layout.fillWidth: true
            text: root.hasItems ? "Current sale (" + root.cart.item_count + ")" : "Current sale"
            color: Theme.text
            elide: Text.ElideRight
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontLarge
            font.weight: Font.DemiBold
        }

        AppButton {
            objectName: "clearSaleButton"
            text: "Clear"
            kind: "ghost"
            enabled: root.hasItems
            onClicked: clearDialog.open()
        }
    }

    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 96

        ListView {
            id: lineList

            anchors.fill: parent
            visible: root.hasItems
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: lineModel
            ScrollBar.vertical: ScrollBar {}

            delegate: Rectangle {
                id: lineRow

                required property int menu_item_id
                required property string name
                required property int quantity
                required property string unit_price_text
                required property string line_total_text
                required property bool available

                objectName: "cartLine-" + menu_item_id
                width: ListView.view.width
                implicitHeight: lineColumn.implicitHeight + Theme.spacingS + Theme.spacingS
                color: "transparent"

                ColumnLayout {
                    id: lineColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.topMargin: Theme.spacingS
                    spacing: 2

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingS

                        Label {
                            Layout.fillWidth: true
                            text: lineRow.name
                            color: Theme.text
                            elide: Text.ElideRight
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.DemiBold
                        }

                        Label {
                            text: lineRow.line_total_text
                            color: Theme.text
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.DemiBold
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingXs

                        Label {
                            Layout.fillWidth: true
                            visible: lineRow.available
                            text: lineRow.unit_price_text + " each"
                            color: Theme.textMuted
                            elide: Text.ElideRight
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                        }

                        StatusChip {
                            visible: !lineRow.available
                            text: "No longer on sale"
                            kind: "danger"
                        }

                        Item {
                            Layout.fillWidth: !lineRow.available
                        }

                        QuantityButton {
                            objectName: "cartMinus-" + lineRow.menu_item_id
                            text: "−"
                            Accessible.name: "One fewer " + lineRow.name
                            onClicked: root.report(
                                api.sales.cartSetQuantity(lineRow.menu_item_id, lineRow.quantity - 1))
                        }

                        Label {
                            Layout.preferredWidth: 52
                            objectName: "cartQuantity-" + lineRow.menu_item_id
                            text: String(lineRow.quantity)
                            color: Theme.text
                            horizontalAlignment: Text.AlignHCenter
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontLarge
                            font.weight: Font.DemiBold
                        }

                        QuantityButton {
                            objectName: "cartPlus-" + lineRow.menu_item_id
                            text: "+"
                            Accessible.name: "One more " + lineRow.name
                            onClicked: root.report(
                                api.sales.cartSetQuantity(lineRow.menu_item_id, lineRow.quantity + 1))
                        }

                        QuantityButton {
                            objectName: "cartRemove-" + lineRow.menu_item_id
                            Layout.leftMargin: Theme.spacingXs
                            quiet: true
                            text: "✕"
                            Accessible.name: "Remove " + lineRow.name
                            onClicked: root.report(api.sales.cartRemove(lineRow.menu_item_id))
                        }
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
        }

        Rectangle {
            objectName: "saleConfirmation"
            anchors.fill: parent
            visible: !root.hasItems && root.lastSale !== null
            radius: Theme.radius
            color: Theme.successSoft

            ColumnLayout {
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spacingM
                spacing: Theme.spacingXs

                Label {
                    Layout.fillWidth: true
                    text: root.lastSale ? "Sale #" + root.lastSale.id + " saved" : ""
                    color: Theme.success
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontLarge
                    font.weight: Font.DemiBold
                }

                Label {
                    Layout.fillWidth: true
                    objectName: "saleConfirmationTotal"
                    text: root.lastSale ? root.lastSale.total_text : ""
                    color: Theme.text
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontDisplay
                    font.weight: Font.DemiBold
                }

                Label {
                    Layout.fillWidth: true
                    text: root.lastSale ? "Paid by " + root.lastSale.payment_method_text
                                          + ". Tap an item to start the next sale." : ""
                    color: Theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                }
            }
        }

        Label {
            anchors.centerIn: parent
            width: parent.width - 2 * Theme.spacingM
            visible: !root.hasItems && root.lastSale === null
            text: "Tap an item to start a sale."
            color: Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2

        TotalRow {
            Layout.fillWidth: true
            objectName: "cartSubtotal"
            label: "Subtotal"
            value: root.cart.subtotal_text || ""
        }

        TotalRow {
            Layout.fillWidth: true
            objectName: "cartTax"
            label: "Tax (" + (root.cart.tax_rate_text || "") + ")"
            value: root.cart.tax_text || ""
        }

        TotalRow {
            Layout.fillWidth: true
            objectName: "cartTotal"
            label: "Total"
            value: root.cart.total_text || ""
            strong: true
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spacingS

        Repeater {
            model: root.paymentMethods

            AbstractButton {
                id: payButton

                required property var modelData
                readonly property bool current: root.paymentMethod === modelData.value

                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.preferredHeight: 56
                objectName: "payment-" + modelData.value
                enabled: root.hasItems
                hoverEnabled: true
                text: modelData.label
                Accessible.name: "Pay by " + modelData.label
                onClicked: root.paymentMethod = modelData.value

                contentItem: Label {
                    text: payButton.text
                    color: !payButton.enabled ? Theme.textDisabled
                           : payButton.current ? Theme.accentPressed : Theme.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                }

                background: Rectangle {
                    radius: Theme.radius
                    color: payButton.current ? Theme.accentSoft
                           : payButton.down ? Theme.border
                           : payButton.hovered ? Theme.surfaceHover : Theme.surface
                    border.width: payButton.current ? 2 : 1
                    border.color: payButton.current ? Theme.accent : Theme.borderStrong
                }
            }
        }
    }

    AppButton {
        Layout.fillWidth: true
        objectName: "confirmSaleButton"
        large: true
        enabled: root.hasItems && root.paymentMethod !== ""
        text: !root.hasItems ? "Confirm sale"
              : root.paymentMethod === "" ? "Choose how they paid"
              : "Confirm sale, " + root.cart.total_text
        onClicked: root.checkoutRequested(root.paymentMethod)
    }
}
