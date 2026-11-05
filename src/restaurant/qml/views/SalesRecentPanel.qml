import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// Recent sales, newest first, with the chosen sale's details. Managers can void from here.
RowLayout {
    id: root

    objectName: "salesRecentPanel"

    readonly property int pageSize: 50
    readonly property int maxPageSize: 500
    readonly property bool canVoid: api.session.permissions.void_sales === true
    readonly property bool seesEveryone: api.session.permissions.view_sales_reports === true

    property var sales: []
    property var selected: null
    property bool hasMore: false

    signal failed(string message)
    signal succeeded(string message)

    function reload() {
        var limit = Math.min(maxPageSize, Math.max(pageSize, sales.length))
        var result = api.sales.listSales("", "", limit, 0)
        if (!result.ok) {
            sales = []
            selected = null
            hasMore = false
            failed(result.error)
            return
        }
        sales = result.data
        hasMore = result.data.length === limit
        if (selected)
            openSale(selected.id)
    }

    function loadMore() {
        var result = api.sales.listSales("", "", pageSize, sales.length)
        if (!result.ok) {
            failed(result.error)
            return
        }
        sales = sales.concat(result.data)
        hasMore = result.data.length === pageSize
    }

    function openSale(saleId) {
        var result = api.sales.getSale(saleId)
        if (result.ok) {
            selected = result.data
        } else {
            selected = null
            failed(result.error)
        }
    }

    function voidSelected(reason) {
        var saleId = selected.id
        var result = api.sales.voidSale(saleId, reason)
        if (result.ok) {
            succeeded("Sale #" + saleId + " was voided and its stock returned.")
        } else {
            failed(result.error)
            reload()
        }
    }

    spacing: Theme.spacingM

    Component.onCompleted: reload()
    onVisibleChanged: {
        if (visible)
            reload()
    }

    Connections {
        target: api
        function onSalesChanged() { root.reload() }
    }

    ConfirmDialog {
        id: voidDialog
        objectName: "voidSaleDialog"
        title: root.selected ? "Void sale #" + root.selected.id + "?" : "Void sale"
        message: root.selected
                 ? "This " + root.selected.total_text + " sale stays on record but no longer "
                   + "counts as revenue, and its stock is returned. This can't be undone."
                 : ""
        inputLabel: "Reason"
        confirmText: "Void sale"
        cancelText: "Keep sale"
        danger: true
        onConfirmed: input => root.voidSelected(input)
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: 3
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
        clip: true

        ListView {
            id: saleList

            anchors.fill: parent
            anchors.margins: 1
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: root.sales
            ScrollBar.vertical: ScrollBar {}

            header: Rectangle {
                width: saleList.width
                implicitHeight: Theme.controlHeight
                color: Theme.surfaceAlt

                Label {
                    anchors.fill: parent
                    leftPadding: Theme.spacingM
                    rightPadding: Theme.spacingM
                    text: root.seesEveryone ? "All sales, newest first"
                                            : "Your sales from the last 7 days"
                    color: Theme.textMuted
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
            }

            delegate: DataRow {
                id: saleRow

                required property var modelData

                objectName: "saleRow-" + modelData.id
                width: ListView.view.width
                implicitHeight: 64
                clickable: true
                selected: root.selected !== null && root.selected.id === modelData.id
                onClicked: root.openSale(modelData.id)

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    Label {
                        Layout.fillWidth: true
                        text: "Sale #" + saleRow.modelData.id + ", "
                              + saleRow.modelData.payment_method_text
                        color: Theme.text
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: saleRow.modelData.created_at_text + ", "
                              + saleRow.modelData.cashier_name
                        color: Theme.textMuted
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                    }
                }

                StatusChip {
                    visible: saleRow.modelData.voided
                    text: "Voided"
                    kind: "danger"
                }

                Label {
                    text: saleRow.modelData.total_text
                    color: saleRow.modelData.voided ? Theme.textMuted : Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                    font.strikeout: saleRow.modelData.voided
                }
            }

            footer: Item {
                width: saleList.width
                implicitHeight: root.hasMore ? Theme.touchHeight : 0

                AppButton {
                    anchors.centerIn: parent
                    objectName: "salesLoadMore"
                    visible: root.hasMore
                    text: "Show older sales"
                    kind: "secondary"
                    onClicked: root.loadMore()
                }
            }

            Label {
                anchors.centerIn: parent
                visible: saleList.count === 0
                text: "No sales to show yet."
                color: Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontBody
            }
        }
    }

    Card {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: 2
        padding: Theme.spacingM

        Label {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.selected === null
            text: "Choose a sale to see its details."
            color: Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.selected !== null
            spacing: Theme.spacingS

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingS

                Label {
                    Layout.fillWidth: true
                    text: root.selected ? "Sale #" + root.selected.id : ""
                    color: Theme.text
                    elide: Text.ElideRight
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontLarge
                    font.weight: Font.DemiBold
                }

                StatusChip {
                    visible: root.selected !== null && root.selected.voided
                    text: "Voided"
                    kind: "danger"
                }
            }

            Label {
                Layout.fillWidth: true
                text: root.selected
                      ? root.selected.created_at_text + "\n" + root.selected.cashier_name + ", paid by "
                        + root.selected.payment_method_text
                      : ""
                color: Theme.textMuted
                wrapMode: Text.Wrap
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
            }

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 48
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: root.selected ? root.selected.items : []
                ScrollBar.vertical: ScrollBar {}

                delegate: RowLayout {
                    required property var modelData

                    width: ListView.view.width
                    height: 32
                    spacing: Theme.spacingS

                    Label {
                        Layout.fillWidth: true
                        text: parent.modelData.quantity + " x " + parent.modelData.name_as_sold
                        color: Theme.text
                        elide: Text.ElideRight
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                    }

                    Label {
                        text: parent.modelData.line_total_text
                        color: Theme.text
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Theme.border
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Theme.spacingS
                rowSpacing: 2

                Label {
                    Layout.fillWidth: true
                    text: "Subtotal"
                    color: Theme.textMuted
                }

                Label {
                    Layout.alignment: Qt.AlignRight
                    text: root.selected ? root.selected.subtotal_text : ""
                    color: Theme.text
                }

                Label {
                    Layout.fillWidth: true
                    text: root.selected ? "Tax (" + root.selected.tax_rate_text + ")" : ""
                    color: Theme.textMuted
                }

                Label {
                    Layout.alignment: Qt.AlignRight
                    text: root.selected ? root.selected.tax_text : ""
                    color: Theme.text
                }

                Label {
                    Layout.fillWidth: true
                    text: "Total"
                    color: Theme.text
                    font.pixelSize: Theme.fontLarge
                    font.weight: Font.DemiBold
                }

                Label {
                    Layout.alignment: Qt.AlignRight
                    objectName: "saleDetailTotal"
                    text: root.selected ? root.selected.total_text : ""
                    color: Theme.text
                    font.pixelSize: Theme.fontLarge
                    font.weight: Font.DemiBold
                }
            }

            MessageBanner {
                Layout.fillWidth: true
                objectName: "voidDetails"
                kind: "warning"
                text: root.selected && root.selected.voided
                      ? "Voided " + root.selected.voided_at_text + " by " + root.selected.voided_by_name
                        + ". Reason: " + root.selected.void_reason
                      : ""
            }

            AppButton {
                Layout.fillWidth: true
                objectName: "voidSaleButton"
                large: true
                kind: "danger"
                text: "Void this sale"
                visible: root.canVoid && root.selected !== null && !root.selected.voided
                onClicked: voidDialog.open()
            }
        }
    }
}
