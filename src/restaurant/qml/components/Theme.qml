pragma Singleton
import QtQuick

// The one place for colours, spacing and type. One restrained accent colour: teal.
QtObject {
    // Surfaces
    readonly property color background: "#F4F5F3"
    readonly property color surface: "#FFFFFF"
    readonly property color surfaceAlt: "#F8F9F7"
    readonly property color surfaceHover: "#EFF2EF"
    readonly property color border: "#DDE1DC"
    readonly property color borderStrong: "#AEB7B1"
    readonly property color scrim: "#66101815"

    // Text
    readonly property color text: "#1B2421"
    readonly property color textMuted: "#56635E"
    readonly property color textDisabled: "#95A09A"
    readonly property color textOnAccent: "#FFFFFF"

    // Sidebar and brand panel
    readonly property color sidebar: "#17211E"
    readonly property color sidebarHover: "#222E2A"
    readonly property color sidebarActive: "#2B3A35"
    readonly property color sidebarText: "#F1F4F2"
    readonly property color sidebarTextMuted: "#A8B5B0"

    // Accent
    readonly property color accent: "#0E7C66"
    readonly property color accentHover: "#0B6B58"
    readonly property color accentPressed: "#09594A"
    readonly property color accentSoft: "#E2F1ED"

    // Status, used only to mean something
    readonly property color danger: "#B42318"
    readonly property color dangerHover: "#9A1E14"
    readonly property color dangerPressed: "#811810"
    readonly property color dangerSoft: "#FBEAE8"
    readonly property color warning: "#8F5B00"
    readonly property color warningSoft: "#FAF0DC"
    readonly property color success: "#1B6E45"
    readonly property color successSoft: "#E3F2EA"
    readonly property color neutralSoft: "#ECEFEC"

    // Spacing
    readonly property int spacingXs: 4
    readonly property int spacingS: 8
    readonly property int spacingM: 16
    readonly property int spacingL: 24
    readonly property int spacingXl: 40

    // Sizes
    readonly property int radius: 8
    readonly property int radiusSmall: 4
    readonly property int controlHeight: 44
    readonly property int touchHeight: 64 // large buttons on the sales screen
    readonly property int rowHeight: 48
    readonly property int sidebarWidth: 216
    readonly property int headerHeight: 64
    readonly property int dialogWidth: 480

    // Type
    readonly property string fontFamily: "Segoe UI"
    readonly property int fontSmall: 13
    readonly property int fontBody: 15
    readonly property int fontLarge: 18
    readonly property int fontTitle: 24
    readonly property int fontDisplay: 34
}
