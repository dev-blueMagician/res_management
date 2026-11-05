import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-S2: with no accounts yet, create the first manager, who is then signed in.
Item {
    id: root

    function create() {
        banner.showResult(api.session.createFirstManager(
            username.text, fullName.text, password.text, confirmation.text))
    }

    Component.onCompleted: fullName.focusInput()

    RowLayout {
        anchors.fill: parent
        spacing: 0

        BrandPanel {
            Layout.preferredWidth: parent.width * 0.42
            Layout.fillHeight: true
            heading: "Welcome"
            caption: "Restaurant Management keeps its records on this computer. "
                     + "Start by creating the manager account."
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(400, parent.width - 2 * Theme.spacingXl)
                spacing: Theme.spacingM

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.bottomMargin: Theme.spacingS
                    spacing: Theme.spacingXs

                    Label {
                        Layout.fillWidth: true
                        text: "Create the manager account"
                        color: Theme.text
                        wrapMode: Text.Wrap
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontTitle
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: "This person can add other staff later."
                        color: Theme.textMuted
                        wrapMode: Text.Wrap
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                    }
                }

                MessageBanner {
                    id: banner
                    Layout.fillWidth: true
                }

                AppTextField {
                    id: fullName
                    Layout.fillWidth: true
                    label: "Full name"
                    onAccepted: username.focusInput()
                }

                AppTextField {
                    id: username
                    Layout.fillWidth: true
                    label: "Username"
                    onAccepted: password.focusInput()
                }

                AppTextField {
                    id: password
                    Layout.fillWidth: true
                    label: "Password (at least 8 characters)"
                    echoMode: TextInput.Password
                    onAccepted: confirmation.focusInput()
                }

                AppTextField {
                    id: confirmation
                    Layout.fillWidth: true
                    label: "Password again"
                    echoMode: TextInput.Password
                    onAccepted: root.create()
                }

                AppButton {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingS
                    text: "Create account and sign in"
                    large: true
                    onClicked: root.create()
                }
            }
        }
    }
}
