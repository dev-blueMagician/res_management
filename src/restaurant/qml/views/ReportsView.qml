import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../components"

// Every report this role may run, one at a time, with a date range where it applies
// and CSV export of exactly what is on screen (FR-R1 to FR-R3).
ColumnLayout {
    id: root

    objectName: "reportsView"

    readonly property var tabLabels: ({
        sales_summary: "Summary",
        sales_by_item: "By item",
        sales_by_category: "By category",
        sales_by_payment_method: "By payment",
        sales_by_cashier: "By cashier",
        stock_levels: "Stock levels",
        stock_value: "Stock value",
        low_stock: "Low stock",
        movement_history: "Movements"
    })
    // Text columns share out the width by how long their values tend to be.
    readonly property var columnFlex: ({
        name: 2.6,
        category: 2,
        full_name: 2.4,
        username: 1.6,
        payment_method: 2,
        total_cents: 1.2,
        voided_quantity: 1.6,
        voided_total_cents: 1.4,
        unit: 1,
        status: 1.4,
        created_at_text: 2.2,
        ingredient: 2.2,
        movement_type: 1.6,
        staff_name: 2.2,
        sale_id: 0.8,
        note: 3.2
    })

    property var available: []
    property string current: ""
    property var report: null
    readonly property string shown: report ? report.name : ""
    property var reportParams: ({})
    property string preset: "today"

    readonly property var salesTabs: available.filter(entry => entry.name.indexOf("sales_") === 0)
    readonly property var stockTabs: available.filter(entry => entry.name.indexOf("sales_") !== 0)
    readonly property bool currentIsDated: {
        for (var i = 0; i < available.length; i++) {
            if (available[i].name === current)
                return available[i].dated
        }
        return false
    }
    readonly property var tableColumns: {
        if (!report)
            return []
        return report.columns.map(column => Object.assign(
            {flex: columnFlex[column.key] || (column.numeric ? 1 : 2)}, column))
    }
    readonly property string emptyText: {
        if (!report)
            return "This report could not be loaded."
        if (shown === "low_stock")
            return "Nothing is low on stock."
        if (shown === "movement_history")
            return "No stock movements in this date range."
        if (shown === "stock_levels" || shown === "stock_value")
            return "No ingredients yet."
        return "No sales in this date range."
    }

    function reload() {
        var result = api.reports.listReports()
        if (!banner.showResult(result)) {
            available = []
            return
        }
        available = result.data
        var requested = api.reports.takeRequestedReport()
        var names = available.map(entry => entry.name)
        if (requested.ok && names.indexOf(requested.data) >= 0)
            selectReport(requested.data)
        else if (names.length > 0)
            selectReport(names[0])
    }

    function selectReport(name) {
        current = name
        run()
    }

    function applyPreset(name) {
        var today = api.reports.localDate(0).data
        fromField.text = name === "week" ? api.reports.localDate(-6).data
                       : name === "month" ? today.slice(0, 8) + "01" : today
        toField.text = today
        preset = name
        run()
    }

    function run() {
        var params = currentIsDated
                     ? {start_date: fromField.text.trim(), end_date: toField.text.trim()} : {}
        var result = api.reports.run(current, params)
        if (banner.showResult(result)) {
            report = result.data
            reportParams = params
        } else {
            report = null
        }
    }

    function exportTo(url) {
        var result = api.reports.exportCsv(report.name, reportParams, url)
        if (result.ok)
            banner.show("Saved " + result.data.row_count + (result.data.row_count === 1 ? " row" : " rows")
                        + " to " + result.data.path, "success")
        else
            banner.show(result.error, "error")
        return result.ok
    }

    function askForExportFile() {
        var range = reportParams.start_date
                    ? "_" + reportParams.start_date + "_to_" + reportParams.end_date : ""
        var folder = StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        saveDialog.selectedFile = folder + "/" + report.name + range + ".csv"
        saveDialog.open()
    }

    spacing: Theme.spacingM

    Component.onCompleted: {
        var today = api.reports.localDate(0)
        if (today.ok) {
            fromField.text = today.data
            toField.text = today.data
            preset = "today"
        }
        reload()
    }

    Connections {
        target: api
        function onSalesChanged() {
            if (root.current !== "")
                root.run()
        }
        function onStockChanged() {
            if (root.current !== "")
                root.run()
        }
    }

    FileDialog {
        id: saveDialog
        objectName: "reportSaveDialog"
        title: "Export report to CSV"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "csv"
        nameFilters: ["CSV files (*.csv)"]
        onAccepted: root.exportTo(selectedFile.toString())
    }

    component ReportTab: AbstractButton {
        id: tab

        property string name: ""
        readonly property bool isCurrent: root.current === name

        objectName: "reportTab-" + name
        text: root.tabLabels[name] || name
        implicitHeight: Theme.controlHeight
        implicitWidth: tabLabel.implicitWidth + 2 * Theme.spacingM
        hoverEnabled: true
        onClicked: root.selectReport(name)

        contentItem: Label {
            id: tabLabel
            text: tab.text
            color: tab.isCurrent ? Theme.textOnAccent : Theme.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            font.weight: Font.DemiBold
        }

        background: Rectangle {
            radius: Theme.radius
            color: tab.isCurrent ? Theme.sidebarActive
                   : tab.down ? Theme.border : tab.hovered ? Theme.surfaceHover : "transparent"
        }
    }

    PageHeader {
        Layout.fillWidth: true
        title: root.report ? root.report.title : "Reports"
        subtitle: !root.report ? ""
                  : !root.reportParams.start_date ? "As of now"
                  : root.reportParams.start_date === root.reportParams.end_date
                    ? root.reportParams.start_date
                    : root.reportParams.start_date + " to " + root.reportParams.end_date

        AppButton {
            objectName: "exportCsvButton"
            text: "Export CSV"
            kind: "secondary"
            enabled: root.report !== null
            onClicked: root.askForExportFile()
        }
    }

    Flow {
        Layout.fillWidth: true
        spacing: Theme.spacingXs

        Repeater {
            model: root.salesTabs

            ReportTab {
                required property var modelData
                name: modelData.name
            }
        }

        Rectangle {
            width: 1
            height: Theme.controlHeight
            visible: root.salesTabs.length > 0 && root.stockTabs.length > 0
            color: Theme.borderStrong
        }

        Repeater {
            model: root.stockTabs

            ReportTab {
                required property var modelData
                name: modelData.name
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: root.currentIsDated
        spacing: Theme.spacingS

        AppButton {
            objectName: "presetToday"
            Layout.alignment: Qt.AlignBottom
            text: "Today"
            kind: root.preset === "today" ? "primary" : "secondary"
            onClicked: root.applyPreset("today")
        }

        AppButton {
            objectName: "presetWeek"
            Layout.alignment: Qt.AlignBottom
            text: "Last 7 days"
            kind: root.preset === "week" ? "primary" : "secondary"
            onClicked: root.applyPreset("week")
        }

        AppButton {
            objectName: "presetMonth"
            Layout.alignment: Qt.AlignBottom
            text: "This month"
            kind: root.preset === "month" ? "primary" : "secondary"
            onClicked: root.applyPreset("month")
        }

        Item {
            Layout.preferredWidth: Theme.spacingS
        }

        AppTextField {
            id: fromField
            objectName: "reportFrom"
            Layout.fillWidth: false
            Layout.preferredWidth: 148
            label: "From"
            placeholderText: "YYYY-MM-DD"
            onTextChanged: root.preset = ""
            onAccepted: root.run()
        }

        AppTextField {
            id: toField
            objectName: "reportTo"
            Layout.fillWidth: false
            Layout.preferredWidth: 148
            label: "To"
            placeholderText: "YYYY-MM-DD"
            onTextChanged: root.preset = ""
            onAccepted: root.run()
        }

        AppButton {
            objectName: "reportApplyDates"
            Layout.alignment: Qt.AlignBottom
            text: "Show"
            kind: "secondary"
            onClicked: root.run()
        }

        Item {
            Layout.fillWidth: true
        }
    }

    MessageBanner {
        id: banner
        objectName: "reportsBanner"
        Layout.fillWidth: true
    }

    // The summary is one row of figures, so it reads better as tiles than as a table.
    GridLayout {
        Layout.fillWidth: true
        visible: root.shown === "sales_summary"
        columns: 3
        rowSpacing: Theme.spacingM
        columnSpacing: Theme.spacingM

        Repeater {
            model: root.shown === "sales_summary"
                   ? [
                       {label: "Total, with tax", value: root.report.summary.total_text, note: "Voided sales excluded"},
                       {label: "Sales", value: String(root.report.summary.sale_count), note: "Not counting voids"},
                       {label: "Subtotal, before tax", value: root.report.summary.subtotal_text, note: ""},
                       {label: "Tax", value: root.report.summary.tax_text, note: ""},
                       {label: "Voided sales", value: String(root.report.summary.void_count), note: "Counted separately"},
                       {label: "Voided total", value: root.report.summary.voided_total_text, note: "Not in the total"}
                   ]
                   : []

            ReportsStatTile {
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.preferredHeight: 120
                label: modelData.label
                value: modelData.value
                note: modelData.note
            }
        }
    }

    ReportsStatTile {
        Layout.preferredWidth: 280
        objectName: "stockValueTotal"
        visible: root.shown === "stock_value"
        label: "Total stock value"
        value: root.shown === "stock_value" ? root.report.summary.total_value_text : ""
        note: "On-hand quantity at unit cost"
    }

    DataTable {
        Layout.fillWidth: true
        Layout.fillHeight: true
        objectName: "reportTable"
        visible: root.shown !== "sales_summary"
        columns: root.tableColumns
        rows: root.report ? root.report.rows : []
        emptyText: root.emptyText
    }

    Label {
        Layout.fillWidth: true
        objectName: "summaryEmpty"
        visible: root.shown === "sales_summary" && root.report.summary.sale_count === 0
                 && root.report.summary.void_count === 0
        text: root.emptyText
        color: Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
    }

    Item {
        Layout.fillHeight: true
        visible: root.shown === "sales_summary"
    }
}
