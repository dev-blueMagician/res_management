import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

FormDialog {
    id: root

    signal passwordChanged()

    title: "Change your password"
    confirmText: "Change password"
    onOpened: current.focusInput()
    onClosed: {
        current.text = ""
        next.text = ""
        confirmation.text = ""
    }
    onSubmitted: {
        var result = api.session.changePassword(current.text, next.text, confirmation.text)
        if (result.ok) {
            close()
            passwordChanged()
        } else {
            errorText = result.error
        }
    }

    AppTextField {
        id: current
        Layout.fillWidth: true
        label: "Current password"
        echoMode: TextInput.Password
    }

    AppTextField {
        id: next
        Layout.fillWidth: true
        label: "New password (at least 8 characters)"
        echoMode: TextInput.Password
    }

    AppTextField {
        id: confirmation
        Layout.fillWidth: true
        label: "New password again"
        echoMode: TextInput.Password
        onAccepted: root.submitted()
    }
}
