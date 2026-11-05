import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

// The dark panel beside the sign-in and first-run forms.
Rectangle {
    id: root

    property string heading: api.settings.restaurantName
    property string caption: "Restaurant Management"

    color: Theme.sidebar

    ColumnLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.spacingXl + Theme.spacingS
        spacing: Theme.spacingS

        Rectangle {
            Layout.preferredWidth: Theme.spacingXl
            Layout.preferredHeight: Theme.spacingXs
            Layout.bottomMargin: Theme.spacingM
            color: Theme.accent
        }

        Label {
            Layout.fillWidth: true
            text: root.heading
            color: Theme.sidebarText
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontDisplay
            font.weight: Font.DemiBold
        }

        Label {
            Layout.fillWidth: true
            text: root.caption
            color: Theme.sidebarTextMuted
            wrapMode: Text.Wrap
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }
    }
}
