import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"

// The shell: first-run screen, sign-in screen, or navigation with the current view.
ApplicationWindow {
    id: window

    width: Math.min(1280, Screen.desktopAvailableWidth)
    height: Math.min(800, Screen.desktopAvailableHeight)
    minimumWidth: 1024
    minimumHeight: 640
    visible: true
    title: api.settings.restaurantName
    color: Theme.background
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody

    Loader {
        anchors.fill: parent
        source: api.session.needsFirstRun ? "views/FirstRunView.qml"
                                          : api.session.signedIn ? "" : "views/LoginView.qml"
        visible: !api.session.signedIn
    }

    Loader {
        anchors.fill: parent
        active: api.session.signedIn
        sourceComponent: shell
    }

    ChangePasswordDialog {
        id: changePasswordDialog
        onPasswordChanged: shellMessage.showFor("Your password was changed.")
    }

    // Lives here so it survives a view change; the shell shows it above the content.
    QtObject {
        id: shellMessage

        property string text: ""

        function showFor(message) {
            text = message
            shellMessageTimer.restart()
        }
    }

    Timer {
        id: shellMessageTimer
        interval: 5000
        onTriggered: shellMessage.text = ""
    }

    Connections {
        target: api.session
        function onChanged() {
            if (!api.session.signedIn)
                changePasswordDialog.close()
        }
    }

    Component {
        id: shell

        RowLayout {
            spacing: 0

            Rectangle {
                Layout.preferredWidth: Theme.sidebarWidth
                Layout.fillHeight: true
                color: Theme.sidebar

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingS + Theme.spacingXs
                    spacing: Theme.spacingXs

                    Label {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Theme.headerHeight - Theme.spacingS - Theme.spacingXs
                        leftPadding: Theme.spacingM
                        text: "Restaurant\nManagement"
                        color: Theme.sidebarTextMuted
                        verticalAlignment: Text.AlignVCenter
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }

                    Repeater {
                        model: api.session.sections

                        NavButton {
                            required property var modelData
                            Layout.fillWidth: true
                            text: modelData.label
                            current: api.session.currentSection === modelData.key
                            onClicked: api.session.goTo(modelData.key)
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.headerHeight
                    color: Theme.surface

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spacingL
                        anchors.rightMargin: Theme.spacingL
                        spacing: Theme.spacingS

                        Label {
                            Layout.fillWidth: true
                            text: api.settings.restaurantName
                            color: Theme.text
                            elide: Text.ElideRight
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontLarge
                            font.weight: Font.DemiBold
                        }

                        ColumnLayout {
                            Layout.rightMargin: Theme.spacingS
                            spacing: 0

                            Label {
                                Layout.alignment: Qt.AlignRight
                                text: api.session.user.full_name
                                color: Theme.text
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                font.weight: Font.DemiBold
                            }

                            Label {
                                Layout.alignment: Qt.AlignRight
                                text: api.session.user.role_text
                                color: Theme.textMuted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                            }
                        }

                        AppButton {
                            text: "Change password"
                            kind: "ghost"
                            onClicked: changePasswordDialog.open()
                        }

                        AppButton {
                            text: "Sign out"
                            kind: "secondary"
                            onClicked: api.session.signOut()
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

                MessageBanner {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.spacingL
                    Layout.rightMargin: Theme.spacingL
                    Layout.topMargin: visible ? Theme.spacingM : 0
                    kind: "success"
                    text: shellMessage.text
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Loader {
                        id: content
                        anchors.fill: parent
                        anchors.margins: Theme.spacingL
                        source: api.session.currentView !== ""
                                ? "views/" + api.session.currentView : ""
                    }

                    Label {
                        anchors.centerIn: parent
                        visible: content.status === Loader.Error
                        text: "This screen could not be loaded. Tell your manager."
                        color: Theme.danger
                    }
                }
            }
        }
    }
}
