import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// Asks before an action. Set inputLabel to also require typed text, such as a void
// reason; confirmed() then carries it. Closes itself, so report errors on the screen.
FormDialog {
    id: root

    property string message: ""
    property string inputLabel: ""

    signal confirmed(string input)

    confirmText: "Confirm"
    confirmEnabled: inputLabel === "" || input.text.trim() !== ""
    onAboutToShow: input.text = ""
    onOpened: {
        if (inputLabel !== "")
            input.focusInput()
    }
    onSubmitted: {
        var typed = input.text.trim()
        close()
        confirmed(typed)
    }

    Label {
        Layout.fillWidth: true
        text: root.message
        visible: root.message !== ""
        wrapMode: Text.Wrap
        color: Theme.text
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
    }

    AppTextField {
        id: input
        Layout.fillWidth: true
        label: root.inputLabel
        visible: root.inputLabel !== ""
    }
}
