import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../components"

// Restaurant details and tax rate; backing up and restoring (NFR-6).
ColumnLayout {
    id: root

    objectName: "settingsView"

    property string pendingRestore: ""

    component SectionTitle: Label {
        color: Theme.text
        font.pixelSize: Theme.fontLarge
        font.weight: Font.DemiBold
    }

    component BodyText: Label {
        Layout.fillWidth: true
        color: Theme.textMuted
        wrapMode: Text.Wrap
    }

    function reload() {
        var result = api.settings.get()
        if (!result.ok) {
            banner.show(result.error, "error")
            return
        }
        restaurantName.text = result.data.restaurant_name
        currencySymbol.text = result.data.currency_symbol
        taxRate.text = result.data.tax_rate_input
    }

    function save() {
        var saved = banner.showResult(api.settings.update({
            restaurant_name: restaurantName.text,
            currency_symbol: currencySymbol.text,
            tax_rate: taxRate.text
        }), "The settings were saved.")
        if (saved)
            reload() // show them as stored
    }

    function backUpNow() {
        var result = api.backup.create()
        banner.showResult(result, result.ok ? "Backup saved: " + result.data.path : "")
    }

    // Step two of restoring, once a file has been chosen.
    function askRestore(fileUrl) {
        pendingRestore = String(fileUrl)
        banner.clear()
        restoreConfirm.open()
    }

    function restore() {
        // On success the session has ended; the sign-in screen replaces this view next turn.
        banner.showResult(api.backup.restore(pendingRestore))
    }

    function fileName(text) {
        return decodeURIComponent(text.substring(text.lastIndexOf("/") + 1))
    }

    Component.onCompleted: reload()
    spacing: Theme.spacingM

    PageHeader {
        Layout.fillWidth: true
        title: "Settings"
        subtitle: "Restaurant details, tax and backups."
    }

    MessageBanner {
        id: banner
        Layout.fillWidth: true
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: false
        Layout.maximumWidth: 1200
        spacing: Theme.spacingM

        Card {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.fillHeight: true

            SectionTitle { text: "Restaurant" }

            AppTextField {
                id: restaurantName
                Layout.fillWidth: true
                label: "Restaurant name"
                maximumLength: 100
                onAccepted: root.save()
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingM

                AppTextField {
                    id: currencySymbol
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    label: "Currency symbol"
                    maximumLength: 3
                    onAccepted: root.save()
                }

                AppTextField {
                    id: taxRate
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    label: "Tax rate (%)"
                    placeholderText: "8.25"
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    onAccepted: root.save()
                }
            }

            BodyText {
                text: "The tax rate is a percentage from 0 to 100. Prices exclude tax, and a new "
                      + "rate applies to new sales only."
                font.pixelSize: Theme.fontSmall
            }

            Item { Layout.fillHeight: true }

            AppButton {
                Layout.alignment: Qt.AlignRight
                text: "Save settings"
                onClicked: root.save()
            }
        }

        Card {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.fillHeight: true

            SectionTitle { text: "Backups" }

            BodyText {
                text: "A dated backup is made each time the app starts, and the last 7 are kept."
            }

            BodyText {
                text: "Backup folder: " + api.backup.folder
                wrapMode: Text.WrapAnywhere
                font.pixelSize: Theme.fontSmall
            }

            AppButton {
                Layout.fillWidth: true
                text: "Back up now"
                kind: "secondary"
                onClicked: root.backUpNow()
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Theme.border
            }

            BodyText {
                text: "Restoring replaces all current data with the chosen backup and signs you out."
            }

            AppButton {
                Layout.fillWidth: true
                text: "Restore from a backup…"
                kind: "danger"
                onClicked: restoreFile.open()
            }
        }
    }

    Item { Layout.fillHeight: true }

    FileDialog {
        id: restoreFile
        title: "Choose a backup to restore"
        fileMode: FileDialog.OpenFile
        currentFolder: api.backup.folderUrl
        nameFilters: ["Backups (*.db)"]
        onAccepted: root.askRestore(selectedFile)
    }

    ConfirmDialog {
        id: restoreConfirm
        title: "Replace all current data?"
        message: "All current data will be replaced with the backup “"
                 + root.fileName(root.pendingRestore) + "”, including staff, menu, stock and "
                 + "sales. Anything recorded since that backup was made will no longer be in the app. "
                 + "A safety copy of the current data is saved first.\n\n"
                 + "You will be signed out, and everyone must sign in with the accounts in that backup."
        confirmText: "Replace data and sign out"
        danger: true
        onConfirmed: root.restore()
    }
}
