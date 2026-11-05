import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-S5: a manager sets someone's password without knowing the current one.
FormDialog {
    id: root

    property int staffId: 0
    property string staffName: ""

    signal saved(string message)

    function openFor(record) {
        staffId = record.id
        staffName = record.full_name
        open()
    }

    function save() {
        var name = staffName
        var result = api.staff.resetPassword(staffId, password.text, confirmation.text)
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved("The password for " + name + " was changed.")
    }

    title: "Reset password: " + staffName
    confirmText: "Reset password"
    confirmEnabled: password.text !== ""
    onOpened: password.focusInput()
    onClosed: {
        password.text = ""
        confirmation.text = ""
    }
    onSubmitted: save()

    AppTextField {
        id: password
        Layout.fillWidth: true
        label: "New password (at least 8 characters)"
        echoMode: TextInput.Password
        onAccepted: confirmation.focusInput()
    }

    AppTextField {
        id: confirmation
        Layout.fillWidth: true
        label: "New password again"
        echoMode: TextInput.Password
        onAccepted: {
            if (root.confirmEnabled)
                root.save()
        }
    }
}
