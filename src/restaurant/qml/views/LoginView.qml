import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-S1: sign in. Also shows why a previous session ended (api.session.notice).
Item {
    id: root

    function signIn() {
        var result = api.session.signIn(username.text, password.text)
        if (result.ok)
            return
        password.text = ""
        banner.showResult(result)
        if (username.text.trim() === "")
            username.focusInput()
        else
            password.focusInput()
    }

    Component.onCompleted: username.focusInput()

    RowLayout {
        anchors.fill: parent
        spacing: 0

        BrandPanel {
            Layout.preferredWidth: parent.width * 0.42
            Layout.fillHeight: true
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(400, parent.width - 2 * Theme.spacingXl)
                spacing: Theme.spacingM

                Label {
                    Layout.fillWidth: true
                    Layout.bottomMargin: Theme.spacingS
                    text: "Sign in"
                    color: Theme.text
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontTitle
                    font.weight: Font.DemiBold
                }

                MessageBanner {
                    Layout.fillWidth: true
                    kind: "info"
                    text: api.session.notice
                    visible: text !== "" && banner.text === ""
                }

                MessageBanner {
                    id: banner
                    Layout.fillWidth: true
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
                    label: "Password"
                    echoMode: TextInput.Password
                    onAccepted: root.signIn()
                }

                AppButton {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingS
                    text: "Sign in"
                    large: true
                    onClicked: root.signIn()
                }
            }
        }
    }
}
